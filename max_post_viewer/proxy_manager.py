"""Менеджер прокси-подключений для аккаунтов Max через GREEN-API."""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional

import aiohttp
from aiohttp_socks import ProxyConnector

logger = logging.getLogger(__name__)


@dataclass
class ProxyAccount:
    """Аккаунт Max, подключённый через GREEN-API с прокси."""
    phone: str
    id_instance: str
    api_token: str
    proxy_url: str
    base_url: str = "https://api.green-api.com"
    session: Optional[aiohttp.ClientSession] = field(default=None, repr=False)
    is_active: bool = True
    fail_count: int = 0

    def api_url(self, method: str) -> str:
        """Сформировать URL для вызова метода GREEN-API."""
        return f"{self.base_url}/waInstance{self.id_instance}/{method}/{self.api_token}"

    async def create_session(self) -> aiohttp.ClientSession:
        """Создать HTTP-сессию через прокси."""
        if self.session and not self.session.closed:
            return self.session

        connector = ProxyConnector.from_url(self.proxy_url)
        self.session = aiohttp.ClientSession(
            connector=connector,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=aiohttp.ClientTimeout(total=15),
        )
        return self.session

    async def close(self):
        """Закрыть сессию."""
        if self.session and not self.session.closed:
            await self.session.close()
            self.session = None

    def mark_failed(self):
        self.fail_count += 1
        if self.fail_count >= 5:
            self.is_active = False
            logger.warning("Аккаунт %s деактивирован после %d ошибок", self.phone, self.fail_count)

    def reset_fails(self):
        self.fail_count = 0


class ProxyManager:
    """Управление пулом прокси-аккаунтов."""

    def __init__(self, accounts: list[ProxyAccount]):
        self._accounts = accounts
        self._lock = asyncio.Lock()

    @property
    def active_accounts(self) -> list[ProxyAccount]:
        return [a for a in self._accounts if a.is_active]

    async def get_sessions(self) -> list[tuple[ProxyAccount, aiohttp.ClientSession]]:
        """Получить активные сессии всех аккаунтов."""
        result = []
        for account in self.active_accounts:
            try:
                session = await account.create_session()
                result.append((account, session))
            except Exception as e:
                logger.error("Не удалось создать сессию для %s: %s", account.phone, e)
                account.mark_failed()
        return result

    async def close_all(self):
        """Закрыть все сессии."""
        for account in self._accounts:
            await account.close()

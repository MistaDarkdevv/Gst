"""Менеджер прокси-подключений для аккаунтов Max."""

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

import aiohttp
from aiohttp_socks import ProxyConnector

logger = logging.getLogger(__name__)


@dataclass
class ProxyAccount:
    """Аккаунт с привязанным прокси."""
    phone: str
    token: str
    proxy_url: str
    session: Optional[aiohttp.ClientSession] = None
    is_active: bool = True
    fail_count: int = 0

    async def create_session(self) -> aiohttp.ClientSession:
        """Создать HTTP-сессию через прокси."""
        if self.session and not self.session.closed:
            return self.session

        connector = ProxyConnector.from_url(self.proxy_url)
        self.session = aiohttp.ClientSession(
            connector=connector,
            headers={
                "User-Agent": "Max/1.0 (Android 14; SDK 34)",
                "Authorization": f"Bearer {self.token}",
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
        if self.fail_count >= 3:
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

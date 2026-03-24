"""Менеджер прокси-подключений для аккаунтов Max (WebSocket)."""

import logging
from dataclasses import dataclass, field
from typing import Optional

import aiohttp
from aiohttp_socks import ProxyConnector

logger = logging.getLogger(__name__)

# Внутренний WebSocket API мессенджера Max
WS_URL = "wss://ws-api.oneme.ru/websocket"
WS_ORIGIN = "https://web.max.ru"


@dataclass
class ProxyAccount:
    """Аккаунт Max с прокси.

    auth_token можно получить:
      1. Открыть https://web.max.ru в браузере
      2. F12 → Application → Local Storage → https://web.max.ru
      3. Скопировать значение ключа __oneme_auth
    """
    phone: str
    auth_token: str
    proxy_url: str
    ws: Optional[aiohttp.ClientWebSocketResponse] = field(default=None, repr=False)
    session: Optional[aiohttp.ClientSession] = field(default=None, repr=False)
    is_active: bool = True
    fail_count: int = 0
    _seq: int = field(default=0, repr=False)

    def next_seq(self) -> int:
        """Получить следующий порядковый номер сообщения."""
        seq = self._seq
        self._seq += 1
        return seq

    async def connect(self) -> aiohttp.ClientWebSocketResponse:
        """Подключиться к WebSocket через прокси."""
        if self.ws and not self.ws.closed:
            return self.ws

        connector = ProxyConnector.from_url(self.proxy_url)
        self.session = aiohttp.ClientSession(connector=connector)
        self.ws = await self.session.ws_connect(
            WS_URL,
            headers={"Origin": WS_ORIGIN},
            heartbeat=30,
        )
        self._seq = 0
        return self.ws

    async def close(self):
        """Закрыть WebSocket и сессию."""
        if self.ws and not self.ws.closed:
            await self.ws.close()
            self.ws = None
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

    @property
    def active_accounts(self) -> list[ProxyAccount]:
        return [a for a in self._accounts if a.is_active]

    async def close_all(self):
        """Закрыть все подключения."""
        for account in self._accounts:
            await account.close()

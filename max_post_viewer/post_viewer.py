"""
Модуль просмотра постов в мессенджере Max через прокси-аккаунты.

Использует GREEN-API (https://green-api.com/v3/docs/) для доступа
к пользовательским аккаунтам Max. Просмотр постов канала реализован
через получение сообщений из чата канала методом GetChatHistory,
что фиксирует просмотр на стороне сервера Max.

Эндпоинты GREEN-API:
  - ReadChat         — отметить чат как прочитанный
  - GetChatHistory   — получить историю сообщений чата (канала)
  - GetMessage       — получить конкретное сообщение
  - lastIncomingMessages — журнал последних входящих сообщений
"""

import asyncio
import logging

import aiohttp

from proxy_manager import ProxyAccount, ProxyManager

logger = logging.getLogger(__name__)


class PostViewer:
    """Просмотр постов канала Max через GREEN-API от имени прокси-аккаунтов."""

    def __init__(self, proxy_manager: ProxyManager, target_channel_id: str, max_retries: int = 3):
        self._pm = proxy_manager
        self._channel_id = target_channel_id
        self._max_retries = max_retries
        self._viewed_ids: set[str] = set()

    async def _api_call(
        self,
        account: ProxyAccount,
        session: aiohttp.ClientSession,
        method: str,
        payload: dict | None = None,
        http_method: str = "POST",
    ) -> dict | list | None:
        """Универсальный вызов GREEN-API метода с retry."""
        url = account.api_url(method)
        for attempt in range(self._max_retries):
            try:
                if http_method == "GET":
                    req = session.get(url)
                else:
                    req = session.post(url, json=payload or {})

                async with req as resp:
                    if resp.status == 200:
                        account.reset_fails()
                        return await resp.json()
                    elif resp.status == 401 or resp.status == 403:
                        logger.error("[%s] Авторизация не удалась (%d) — проверьте idInstance/apiToken", account.phone, resp.status)
                        account.is_active = False
                        return None
                    elif resp.status == 429:
                        wait = 2 ** (attempt + 1)
                        logger.warning("[%s] Rate limit, ожидание %dс", account.phone, wait)
                        await asyncio.sleep(wait)
                    elif resp.status == 466:
                        logger.error("[%s] Аккаунт не авторизован в Max (466). Авторизуйте аккаунт в GREEN-API", account.phone)
                        account.is_active = False
                        return None
                    else:
                        body = await resp.text()
                        logger.warning("[%s] %s вернул %d: %s", account.phone, method, resp.status, body[:200])
                        account.mark_failed()
                        return None
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.error("[%s] Ошибка сети при %s (попытка %d): %s", account.phone, method, attempt + 1, e)
                account.mark_failed()
                if attempt < self._max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
        return None

    async def read_chat(self, account: ProxyAccount, session: aiohttp.ClientSession) -> bool:
        """Отметить чат канала как прочитанный (ReadChat).

        POST /waInstance{id}/readChat/{token}
        Body: {"chatId": "..."}
        Это помечает все сообщения канала как просмотренные.
        """
        result = await self._api_call(account, session, "readChat", {"chatId": self._channel_id})
        if result is not None:
            logger.info("[%s] Канал %s отмечен как прочитанный", account.phone, self._channel_id)
            return True
        return False

    async def get_chat_history(self, account: ProxyAccount, session: aiohttp.ClientSession, count: int = 50) -> list[dict]:
        """Получить историю сообщений канала (GetChatHistory).

        POST /waInstance{id}/getChatHistory/{token}
        Body: {"chatId": "...", "count": 50}
        Сам вызов фиксирует просмотр сообщений на стороне сервера.
        """
        result = await self._api_call(account, session, "getChatHistory", {
            "chatId": self._channel_id,
            "count": count,
        })
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            return result.get("messages", result.get("data", []))
        return []

    async def get_last_incoming(self, account: ProxyAccount, session: aiohttp.ClientSession) -> list[dict]:
        """Получить последние входящие сообщения (lastIncomingMessages).

        GET /waInstance{id}/lastIncomingMessages/{token}
        Возвращает журнал входящих — включает сообщения из каналов.
        """
        result = await self._api_call(account, session, "lastIncomingMessages", http_method="GET")
        if isinstance(result, list):
            return [m for m in result if m.get("chatId") == self._channel_id]
        return []

    async def view_posts_with_account(self, account: ProxyAccount, session: aiohttp.ClientSession) -> int:
        """Просмотреть все посты канала одним аккаунтом.

        Стратегия:
        1. Получаем историю чата (getChatHistory) — это фиксирует просмотр
        2. Отмечаем чат как прочитанный (readChat)
        3. Дополнительно проверяем через lastIncomingMessages
        """
        # Шаг 1: Получить историю — сам запрос уже засчитывает просмотр
        messages = await self.get_chat_history(account, session)
        if not messages:
            logger.warning("[%s] Нет сообщений в канале %s", account.phone, self._channel_id)
            # Попробуем через lastIncomingMessages
            messages = await self.get_last_incoming(account, session)

        viewed = 0
        for msg in messages:
            msg_id = msg.get("idMessage", msg.get("id", ""))
            if msg_id and msg_id not in self._viewed_ids:
                self._viewed_ids.add(msg_id)
                viewed += 1

        # Шаг 2: Отметить весь чат как прочитанный
        await asyncio.sleep(1)
        await self.read_chat(account, session)

        logger.info("[%s] Просмотрено %d постов в канале %s", account.phone, viewed, self._channel_id)
        return viewed

    async def run_viewing_cycle(self) -> dict[str, int]:
        """Запустить один цикл просмотра всеми аккаунтами."""
        sessions = await self._pm.get_sessions()
        if not sessions:
            logger.error("Нет активных аккаунтов для просмотра")
            return {}

        results: dict[str, int] = {}
        tasks = []
        for account, session in sessions:
            tasks.append(self._run_for_account(account, session, results))
        await asyncio.gather(*tasks)
        return results

    async def _run_for_account(self, account: ProxyAccount, session: aiohttp.ClientSession, results: dict):
        try:
            # Случайная задержка между аккаунтами (0-5с) для естественности
            delay = hash(account.phone) % 5
            await asyncio.sleep(abs(delay))
            count = await self.view_posts_with_account(account, session)
            results[account.phone] = count
        except Exception as e:
            logger.error("Непредвиденная ошибка для %s: %s", account.phone, e)
            results[account.phone] = 0

"""
Просмотр постов в мессенджере Max через внутренний WebSocket API.

Протокол: wss://ws-api.oneme.ru/websocket
Origin: https://web.max.ru

Opcodes:
  1  — heartbeat (keepalive)
  6  — handshake (первое сообщение)
  19 — authenticate (авторизация по токену)
  49 — get_history (получить историю чата/канала)
  50 — mark_as_read (отметить сообщения как прочитанные = ПРОСМОТР)
  75 — subscribe_to_chat (подписка на обновления чата)
"""

import asyncio
import json
import logging

import aiohttp

from proxy_manager import ProxyAccount, ProxyManager

logger = logging.getLogger(__name__)

# Opcodes внутреннего API Max
OP_HEARTBEAT = 1
OP_HANDSHAKE = 6
OP_AUTHENTICATE = 19
OP_GET_HISTORY = 49
OP_MARK_AS_READ = 50
OP_SUBSCRIBE_CHAT = 75


class MaxWSClient:
    """Клиент WebSocket API Max для одного аккаунта."""

    def __init__(self, account: ProxyAccount, timeout: float = 10.0):
        self.account = account
        self.timeout = timeout

    async def _send(self, opcode: int, payload: dict) -> dict | None:
        """Отправить сообщение и получить ответ."""
        ws = self.account.ws
        if not ws or ws.closed:
            return None

        msg = {
            "seq": self.account.next_seq(),
            "opcode": opcode,
            "payload": payload,
        }
        await ws.send_json(msg)

        try:
            resp = await asyncio.wait_for(ws.receive_json(), timeout=self.timeout)
            return resp
        except asyncio.TimeoutError:
            logger.warning("[%s] Таймаут ожидания ответа (opcode %d)", self.account.phone, opcode)
            return None
        except TypeError:
            # ws.receive_json() может вернуть не JSON при закрытии
            return None

    async def handshake(self) -> bool:
        """Отправить handshake (opcode 6) — первое сообщение."""
        resp = await self._send(OP_HANDSHAKE, {
            "userAgent": {"deviceType": "WEB"},
        })
        if resp is not None:
            logger.debug("[%s] Handshake OK", self.account.phone)
            return True
        logger.error("[%s] Handshake не удался", self.account.phone)
        return False

    async def authenticate(self) -> bool:
        """Авторизоваться по токену (opcode 19)."""
        resp = await self._send(OP_AUTHENTICATE, {
            "interactive": True,
            "token": self.account.auth_token,
            "chatsSync": 0,
            "contactsSync": 0,
            "presenceSync": 0,
            "draftsSync": 0,
            "chatsCount": 40,
        })
        if resp is None:
            logger.error("[%s] Нет ответа на authenticate", self.account.phone)
            return False

        payload = resp.get("payload", {})
        # Проверяем наличие данных профиля в ответе
        if payload.get("chats") is not None or payload.get("token") is not None:
            logger.info("[%s] Авторизация успешна", self.account.phone)
            self.account.reset_fails()
            return True

        error = payload.get("error", resp.get("error", "unknown"))
        logger.error("[%s] Ошибка авторизации: %s", self.account.phone, error)
        self.account.is_active = False
        return False

    async def get_history(self, chat_id: str, count: int = 50) -> list[dict]:
        """Получить историю сообщений чата/канала (opcode 49).

        Сам запрос истории фиксирует просмотр на стороне сервера.
        """
        resp = await self._send(OP_GET_HISTORY, {
            "chatId": chat_id,
            "count": count,
        })
        if resp is None:
            return []

        payload = resp.get("payload", {})
        messages = payload.get("messages", payload.get("data", []))
        if isinstance(messages, list):
            return messages
        return []

    async def mark_as_read(self, chat_id: str, message_id: str) -> bool:
        """Отметить сообщение как прочитанное (opcode 50) — засчитывает просмотр."""
        resp = await self._send(OP_MARK_AS_READ, {
            "chatId": chat_id,
            "messageId": message_id,
        })
        return resp is not None

    async def subscribe_to_chat(self, chat_id: str) -> bool:
        """Подписаться на обновления чата (opcode 75)."""
        resp = await self._send(OP_SUBSCRIBE_CHAT, {
            "chatId": chat_id,
            "subscribe": True,
        })
        return resp is not None

    async def send_heartbeat(self) -> bool:
        """Отправить heartbeat (opcode 1)."""
        resp = await self._send(OP_HEARTBEAT, {"interactive": False})
        return resp is not None


class PostViewer:
    """Просмотр постов канала Max через WebSocket API от имени прокси-аккаунтов."""

    def __init__(self, proxy_manager: ProxyManager, target_channel_id: str, posts_count: int = 50):
        self._pm = proxy_manager
        self._channel_id = target_channel_id
        self._posts_count = posts_count

    async def _connect_and_auth(self, account: ProxyAccount) -> MaxWSClient | None:
        """Подключить и авторизовать один аккаунт."""
        try:
            await account.connect()
        except Exception as e:
            logger.error("[%s] Не удалось подключиться через прокси: %s", account.phone, e)
            account.mark_failed()
            return None

        client = MaxWSClient(account)

        if not await client.handshake():
            account.mark_failed()
            await account.close()
            return None

        if not await client.authenticate():
            await account.close()
            return None

        return client

    async def view_posts_with_account(self, account: ProxyAccount) -> int:
        """Просмотреть все посты канала одним аккаунтом.

        Стратегия:
        1. Подключиться через WebSocket + прокси
        2. Handshake → Authenticate
        3. Подписаться на чат канала (subscribe_to_chat)
        4. Получить историю (get_history) — фиксирует просмотр
        5. Отметить последнее сообщение как прочитанное (mark_as_read)
        """
        client = await self._connect_and_auth(account)
        if not client:
            return 0

        try:
            # Подписываемся на канал
            await client.subscribe_to_chat(self._channel_id)
            await asyncio.sleep(0.5)

            # Получаем историю — сам запрос засчитывает просмотр
            messages = await client.get_history(self._channel_id, count=self._posts_count)
            if not messages:
                logger.warning("[%s] Нет сообщений в канале %s", account.phone, self._channel_id)
                return 0

            logger.info("[%s] Получено %d постов из канала %s", account.phone, len(messages), self._channel_id)

            # Отмечаем каждый пост как прочитанный
            viewed = 0
            for msg in messages:
                msg_id = msg.get("mid", msg.get("messageId", msg.get("id", "")))
                if not msg_id:
                    continue

                await asyncio.sleep(0.3)  # Небольшая задержка между mark_as_read
                if await client.mark_as_read(self._channel_id, str(msg_id)):
                    viewed += 1

            logger.info("[%s] Просмотрено %d постов", account.phone, viewed)
            return viewed

        except Exception as e:
            logger.error("[%s] Ошибка при просмотре: %s", account.phone, e)
            account.mark_failed()
            return 0
        finally:
            await account.close()

    async def run_viewing_cycle(self) -> dict[str, int]:
        """Запустить один цикл просмотра всеми аккаунтами."""
        active = self._pm.active_accounts
        if not active:
            logger.error("Нет активных аккаунтов для просмотра")
            return {}

        results: dict[str, int] = {}

        # Запускаем параллельно, но с рандомной задержкой между аккаунтами
        tasks = []
        for i, account in enumerate(active):
            tasks.append(self._run_for_account(account, results, delay=i * 2))

        await asyncio.gather(*tasks)
        return results

    async def _run_for_account(self, account: ProxyAccount, results: dict, delay: float = 0):
        if delay > 0:
            await asyncio.sleep(delay)
        try:
            count = await self.view_posts_with_account(account)
            results[account.phone] = count
        except Exception as e:
            logger.error("Непредвиденная ошибка для %s: %s", account.phone, e)
            results[account.phone] = 0

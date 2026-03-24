"""
Просмотр постов в мессенджере Max через внутренний WebSocket API.

Протокол: wss://ws-api.oneme.ru/websocket
Origin: https://web.max.ru

Просмотры (глазик) засчитываются сервером по совокупности:
  1. Телеметрия навигации (Opcode.LOG=5) — клиент сообщает
     что пользователь перешёл на экран канала
  2. Запрос истории чата (CHAT_HISTORY=49) — сервер видит
     что пользователь загрузил посты
  3. Отметка прочитанным (CHAT_MARK=50) — подтверждение

Полный список opcodes (из PyMax / fresh-milkshake):
  1=PING, 5=LOG, 6=SESSION_INIT, 19=LOGIN, 49=CHAT_HISTORY,
  50=CHAT_MARK, 74=MSG_GET_STAT, 75=CHAT_SUBSCRIBE, и др.
"""

import asyncio
import json
import logging
import time
import uuid
import random

import aiohttp

from proxy_manager import ProxyAccount, ProxyManager

logger = logging.getLogger(__name__)


# Opcodes внутреннего WebSocket API Max (из PyMax enum.py)
class Op:
    PING = 1
    LOG = 5            # Телеметрия / навигация
    SESSION_INIT = 6   # Handshake
    LOGIN = 19         # Авторизация по токену
    CHAT_HISTORY = 49  # Получить историю чата (фиксирует просмотр)
    CHAT_MARK = 50     # Отметить как прочитанное
    MSG_GET_STAT = 74  # Статистика сообщения
    CHAT_SUBSCRIBE = 75  # Подписка на обновления чата


class MaxWSClient:
    """Клиент WebSocket API Max для одного аккаунта."""

    def __init__(self, account: ProxyAccount, timeout: float = 10.0):
        self.account = account
        self.timeout = timeout
        self._session_id = str(uuid.uuid4())

    async def _send(self, opcode: int, payload: dict) -> dict | None:
        """Отправить сообщение и получить ответ."""
        ws = self.account.ws
        if not ws or ws.closed:
            return None

        msg = {
            "cmd": 0,
            "seq": self.account.next_seq(),
            "opcode": opcode,
            "payload": payload,
        }
        await ws.send_json(msg)

        try:
            resp = await asyncio.wait_for(ws.receive_json(), timeout=self.timeout)
            return resp
        except asyncio.TimeoutError:
            logger.warning("[%s] Таймаут (opcode %d)", self.account.phone, opcode)
            return None
        except TypeError:
            return None

    async def _send_no_wait(self, opcode: int, payload: dict):
        """Отправить без ожидания ответа (для телеметрии)."""
        ws = self.account.ws
        if not ws or ws.closed:
            return
        msg = {
            "cmd": 0,
            "seq": self.account.next_seq(),
            "opcode": opcode,
            "payload": payload,
        }
        await ws.send_json(msg)

    # ─── Подключение и авторизация ───

    async def handshake(self) -> bool:
        """SESSION_INIT (opcode 6) — первое сообщение."""
        resp = await self._send(Op.SESSION_INIT, {
            "userAgent": {"deviceType": "WEB"},
        })
        if resp is not None:
            logger.debug("[%s] Handshake OK", self.account.phone)
            return True
        logger.error("[%s] Handshake не удался", self.account.phone)
        return False

    async def authenticate(self) -> bool:
        """LOGIN (opcode 19) — авторизация по токену."""
        resp = await self._send(Op.LOGIN, {
            "interactive": True,
            "token": self.account.auth_token,
            "chatsSync": 0,
            "contactsSync": 0,
            "presenceSync": 0,
            "draftsSync": 0,
            "chatsCount": 40,
        })
        if resp is None:
            logger.error("[%s] Нет ответа на login", self.account.phone)
            return False

        payload = resp.get("payload", {})
        if payload.get("chats") is not None or payload.get("token") is not None:
            logger.info("[%s] Авторизация успешна", self.account.phone)
            self.account.reset_fails()
            return True

        error = payload.get("error", "unknown")
        logger.error("[%s] Ошибка авторизации: %s", self.account.phone, error)
        self.account.is_active = False
        return False

    # ─── Телеметрия навигации (ключ к просмотрам!) ───

    async def send_navigation(self, screen_from: str, screen_to: str):
        """LOG (opcode 5) — отправить событие навигации.

        Это то, что отправляет клиент Max когда пользователь переходит
        между экранами. Серверу это говорит "пользователь сейчас смотрит
        на этот экран", что учитывается в счётчике просмотров.
        """
        action_id = str(uuid.uuid4())
        await self._send_no_wait(Op.LOG, {
            "events": [{
                "type": "NAV",
                "userId": "",
                "timestamp": int(time.time() * 1000),
                "actionId": action_id,
                "screenFrom": screen_from,
                "screenTo": screen_to,
                "sessionId": self._session_id,
            }],
        })

    async def send_cold_start(self):
        """Отправить событие холодного старта (открытие приложения)."""
        action_id = str(uuid.uuid4())
        await self._send_no_wait(Op.LOG, {
            "events": [{
                "type": "COLD_START",
                "userId": "",
                "timestamp": int(time.time() * 1000),
                "actionId": action_id,
                "screenFrom": "",
                "screenTo": "chats_list_tab",
                "sessionId": self._session_id,
            }],
        })

    # ─── Работа с каналом ───

    async def subscribe_to_chat(self, chat_id: str) -> bool:
        """CHAT_SUBSCRIBE (opcode 75)."""
        resp = await self._send(Op.CHAT_SUBSCRIBE, {
            "chatId": chat_id,
            "subscribe": True,
        })
        return resp is not None

    async def get_history(self, chat_id: str, count: int = 50) -> list[dict]:
        """CHAT_HISTORY (opcode 49) — получить историю.

        Сервер фиксирует что этот пользователь загрузил посты канала.
        """
        resp = await self._send(Op.CHAT_HISTORY, {
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
        """CHAT_MARK (opcode 50) — отметить как прочитанное."""
        resp = await self._send(Op.CHAT_MARK, {
            "chatId": chat_id,
            "messageId": message_id,
        })
        return resp is not None

    async def heartbeat(self):
        """PING (opcode 1)."""
        await self._send_no_wait(Op.PING, {"interactive": False})


class PostViewer:
    """Просмотр постов канала Max через WebSocket API.

    Имитирует реальное поведение пользователя:
    1. Подключается и авторизуется
    2. Отправляет COLD_START телеметрию
    3. "Переходит" на экран списка чатов → канал (NAV телеметрия)
    4. Загружает историю канала (CHAT_HISTORY)
    5. "Прокручивает" посты с паузами (NAV + задержки)
    6. Отмечает последний пост как прочитанный (CHAT_MARK)
    """

    def __init__(self, proxy_manager: ProxyManager, target_channel_id: str, posts_count: int = 50):
        self._pm = proxy_manager
        self._channel_id = target_channel_id
        self._posts_count = posts_count

    async def _connect_and_auth(self, account: ProxyAccount) -> MaxWSClient | None:
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
        """Полный цикл просмотра постов одним аккаунтом."""
        client = await self._connect_and_auth(account)
        if not client:
            return 0

        try:
            # 1. Телеметрия: "приложение открылось"
            await client.send_cold_start()
            await asyncio.sleep(random.uniform(0.5, 1.5))

            # 2. Телеметрия: "перешёл в список чатов"
            await client.send_navigation("", "chats_list_tab")
            await asyncio.sleep(random.uniform(1.0, 3.0))

            # 3. Подписаться на канал
            await client.subscribe_to_chat(self._channel_id)
            await asyncio.sleep(random.uniform(0.3, 0.8))

            # 4. Телеметрия: "открыл канал" — ключевой момент!
            await client.send_navigation("chats_list_tab", f"channel_{self._channel_id}")
            await asyncio.sleep(random.uniform(0.5, 1.0))

            # 5. Загрузить историю — сервер засчитывает просмотр
            messages = await client.get_history(self._channel_id, count=self._posts_count)
            if not messages:
                logger.warning("[%s] Нет сообщений в канале %s", account.phone, self._channel_id)
                return 0

            logger.info("[%s] Получено %d постов из канала %s", account.phone, len(messages), self._channel_id)

            # 6. Имитация прокрутки: читаем посты с паузами
            viewed = len(messages)
            last_msg_id = None
            for msg in messages:
                msg_id = msg.get("mid", msg.get("messageId", msg.get("id", "")))
                if msg_id:
                    last_msg_id = str(msg_id)
                # Пауза "чтения" поста — как будто пользователь скроллит
                await asyncio.sleep(random.uniform(0.5, 2.0))
                # Heartbeat чтобы соединение не закрылось
                await client.heartbeat()

            # 7. Отмечаем последний пост прочитанным
            if last_msg_id:
                await client.mark_as_read(self._channel_id, last_msg_id)

            # 8. Телеметрия: "вышел из канала"
            await client.send_navigation(f"channel_{self._channel_id}", "chats_list_tab")

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
        tasks = []
        for i, account in enumerate(active):
            # Задержка между аккаунтами: 2-5с чтобы не было одновременных подключений
            delay = i * random.uniform(2.0, 5.0)
            tasks.append(self._run_for_account(account, results, delay=delay))

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

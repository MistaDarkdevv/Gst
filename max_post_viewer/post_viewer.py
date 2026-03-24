"""Модуль просмотра постов в мессенджере Max через прокси-аккаунты."""

import asyncio
import logging
from typing import Optional

import aiohttp

from proxy_manager import ProxyAccount, ProxyManager

logger = logging.getLogger(__name__)

# Базовый URL API Max (замените на актуальный)
MAX_API_BASE = "https://api.max.ru/v1"


class PostViewer:
    """Просмотр постов целевого пользователя через прокси-аккаунты."""

    def __init__(self, proxy_manager: ProxyManager, target_user_id: str, max_retries: int = 3):
        self._pm = proxy_manager
        self._target_user_id = target_user_id
        self._max_retries = max_retries
        self._viewed_posts: set[str] = set()

    async def fetch_posts(self, account: ProxyAccount, session: aiohttp.ClientSession) -> list[dict]:
        """Получить список постов целевого пользователя."""
        url = f"{MAX_API_BASE}/users/{self._target_user_id}/posts"
        for attempt in range(self._max_retries):
            try:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        account.reset_fails()
                        return data.get("posts", [])
                    elif resp.status == 401:
                        logger.error("Токен аккаунта %s невалиден (401)", account.phone)
                        account.is_active = False
                        return []
                    elif resp.status == 429:
                        wait = 2 ** (attempt + 1)
                        logger.warning("Rate limit для %s, ожидание %dс", account.phone, wait)
                        await asyncio.sleep(wait)
                    else:
                        logger.warning("Ответ %d от API для %s", resp.status, account.phone)
                        account.mark_failed()
                        return []
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.error("Ошибка сети для %s (попытка %d): %s", account.phone, attempt + 1, e)
                account.mark_failed()
                if attempt < self._max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
        return []

    async def view_post(self, account: ProxyAccount, session: aiohttp.ClientSession, post_id: str) -> bool:
        """Просмотреть конкретный пост."""
        url = f"{MAX_API_BASE}/posts/{post_id}/view"
        for attempt in range(self._max_retries):
            try:
                async with session.post(url) as resp:
                    if resp.status in (200, 204):
                        logger.info("[%s] Пост %s просмотрен", account.phone, post_id)
                        account.reset_fails()
                        return True
                    elif resp.status == 429:
                        await asyncio.sleep(2 ** (attempt + 1))
                    else:
                        logger.warning("[%s] Не удалось просмотреть пост %s: %d", account.phone, post_id, resp.status)
                        account.mark_failed()
                        return False
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.error("[%s] Ошибка при просмотре поста %s: %s", account.phone, post_id, e)
                account.mark_failed()
                if attempt < self._max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
        return False

    async def view_all_posts_with_account(self, account: ProxyAccount, session: aiohttp.ClientSession) -> int:
        """Просмотреть все посты одним аккаунтом. Возвращает количество просмотренных."""
        posts = await self.fetch_posts(account, session)
        if not posts:
            return 0

        viewed = 0
        for post in posts:
            post_id = post.get("id", "")
            if not post_id:
                continue
            # Добавляем случайную задержку между просмотрами (1-3с)
            delay = 1 + (hash(post_id + account.phone) % 3)
            await asyncio.sleep(delay)

            if await self.view_post(account, session, post_id):
                viewed += 1
                self._viewed_posts.add(post_id)
        return viewed

    async def run_viewing_cycle(self) -> dict[str, int]:
        """Запустить один цикл просмотра всеми аккаунтами."""
        sessions = await self._pm.get_sessions()
        if not sessions:
            logger.error("Нет активных аккаунтов для просмотра")
            return {}

        results: dict[str, int] = {}

        # Запускаем просмотр параллельно по всем аккаунтам
        tasks = []
        for account, session in sessions:
            tasks.append(self._run_for_account(account, session, results))

        await asyncio.gather(*tasks)
        return results

    async def _run_for_account(self, account: ProxyAccount, session: aiohttp.ClientSession, results: dict):
        try:
            count = await self.view_all_posts_with_account(account, session)
            results[account.phone] = count
        except Exception as e:
            logger.error("Непредвиденная ошибка для %s: %s", account.phone, e)
            results[account.phone] = 0

#!/usr/bin/env python3
"""
Max Post Viewer — автоматический просмотр постов через прокси-аккаунты.

Использует GREEN-API для доступа к пользовательским аккаунтам Max.
Каждый аккаунт подключается через свой прокси для изоляции.

Использование:
    python main.py --config config.json
    python main.py --config config.json --once   # один цикл без повторов

Перед запуском:
    1. Зарегистрируйтесь на https://green-api.com и создайте инстансы
    2. Авторизуйте каждый аккаунт Max в GREEN-API
    3. Заполните config.json (см. config.example.json)
    4. pip install -r requirements.txt
"""

import argparse
import asyncio
import json
import logging
import signal
import sys
from pathlib import Path

from proxy_manager import ProxyAccount, ProxyManager
from post_viewer import PostViewer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

_shutdown = asyncio.Event()


def handle_signal(sig, frame):
    logger.info("Получен сигнал завершения, останавливаем...")
    _shutdown.set()


def load_config(path: str) -> dict:
    config_path = Path(path)
    if not config_path.exists():
        logger.error("Файл конфигурации не найден: %s", path)
        sys.exit(1)
    with open(config_path, encoding="utf-8") as f:
        return json.load(f)


def build_accounts(config: dict) -> list[ProxyAccount]:
    base_url = config.get("green_api_base_url", "https://api.green-api.com")
    accounts = []
    for acc in config.get("accounts", []):
        accounts.append(
            ProxyAccount(
                phone=acc["phone"],
                id_instance=acc["id_instance"],
                api_token=acc["api_token"],
                proxy_url=acc["proxy"],
                base_url=base_url,
            )
        )
    if not accounts:
        logger.error("В конфигурации нет аккаунтов")
        sys.exit(1)
    return accounts


async def check_accounts(pm: ProxyManager):
    """Проверить статус всех аккаунтов перед началом работы."""
    sessions = await pm.get_sessions()
    for account, session in sessions:
        url = account.api_url("getStateInstance")
        try:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    state = data.get("stateInstance", "unknown")
                    logger.info("[%s] Статус: %s", account.phone, state)
                    if state != "authorized":
                        logger.warning("[%s] Аккаунт не авторизован! Авторизуйте в GREEN-API", account.phone)
                        account.is_active = False
                else:
                    logger.error("[%s] Не удалось проверить статус: %d", account.phone, resp.status)
                    account.mark_failed()
        except Exception as e:
            logger.error("[%s] Ошибка проверки: %s", account.phone, e)
            account.mark_failed()


async def main_loop(viewer: PostViewer, pm: ProxyManager, interval: int, run_once: bool):
    """Основной цикл просмотра постов."""
    # Проверяем аккаунты перед стартом
    logger.info("Проверка статуса аккаунтов...")
    await check_accounts(pm)

    active = pm.active_accounts
    if not active:
        logger.error("Нет авторизованных аккаунтов. Завершаем.")
        await pm.close_all()
        return

    logger.info("Готово к работе. Авторизованных аккаунтов: %d", len(active))

    cycle = 0
    try:
        while not _shutdown.is_set():
            cycle += 1
            active = pm.active_accounts
            logger.info("=== Цикл %d | Активных аккаунтов: %d ===", cycle, len(active))

            if not active:
                logger.error("Все аккаунты деактивированы, завершаем работу")
                break

            results = await viewer.run_viewing_cycle()

            total = sum(results.values())
            logger.info("Цикл %d завершён. Просмотрено постов: %d", cycle, total)
            for phone, count in results.items():
                logger.info("  %s: %d постов", phone, count)

            if run_once:
                break

            # Ждём интервал или сигнал остановки
            try:
                await asyncio.wait_for(_shutdown.wait(), timeout=interval)
                break
            except asyncio.TimeoutError:
                pass
    finally:
        await pm.close_all()


def main():
    parser = argparse.ArgumentParser(description="Max Post Viewer — просмотр постов через прокси-аккаунты (GREEN-API)")
    parser.add_argument("--config", required=True, help="Путь к файлу конфигурации JSON")
    parser.add_argument("--once", action="store_true", help="Выполнить один цикл и завершить")
    args = parser.parse_args()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    config = load_config(args.config)
    accounts = build_accounts(config)
    interval = config.get("view_interval_seconds", 60)
    max_retries = config.get("max_retries", 3)
    channel_id = config.get("target_channel_id", "")

    if not channel_id:
        logger.error("target_channel_id не указан в конфигурации")
        sys.exit(1)

    logger.info("Загружено аккаунтов: %d", len(accounts))
    logger.info("Целевой канал: %s", channel_id)
    logger.info("Интервал между циклами: %dс", interval)

    pm = ProxyManager(accounts)
    viewer = PostViewer(pm, channel_id, max_retries=max_retries)

    asyncio.run(main_loop(viewer, pm, interval, args.once))
    logger.info("Работа завершена.")


if __name__ == "__main__":
    main()

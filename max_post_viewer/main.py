#!/usr/bin/env python3
"""
Max Post Viewer — автоматический просмотр постов через прокси-аккаунты.

Использование:
    python main.py --config config.json
    python main.py --config config.json --once   # один цикл без повторов
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
    accounts = []
    for acc in config.get("accounts", []):
        accounts.append(
            ProxyAccount(
                phone=acc["phone"],
                token=acc["token"],
                proxy_url=acc["proxy"],
            )
        )
    if not accounts:
        logger.error("В конфигурации нет аккаунтов")
        sys.exit(1)
    return accounts


async def main_loop(viewer: PostViewer, pm: ProxyManager, interval: int, run_once: bool):
    """Основной цикл просмотра постов."""
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
                break  # shutdown was set
            except asyncio.TimeoutError:
                pass  # нормальный таймаут — продолжаем
    finally:
        await pm.close_all()


def main():
    parser = argparse.ArgumentParser(description="Max Post Viewer — просмотр постов через прокси-аккаунты")
    parser.add_argument("--config", required=True, help="Путь к файлу конфигурации JSON")
    parser.add_argument("--once", action="store_true", help="Выполнить один цикл и завершить")
    args = parser.parse_args()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    config = load_config(args.config)
    accounts = build_accounts(config)
    interval = config.get("view_interval_seconds", 30)
    max_retries = config.get("max_retries", 3)
    target_user_id = config.get("target_user_id", "")

    if not target_user_id:
        logger.error("target_user_id не указан в конфигурации")
        sys.exit(1)

    logger.info("Загружено аккаунтов: %d", len(accounts))
    logger.info("Целевой пользователь: %s", target_user_id)
    logger.info("Интервал между циклами: %dс", interval)

    pm = ProxyManager(accounts)
    viewer = PostViewer(pm, target_user_id, max_retries=max_retries)

    asyncio.run(main_loop(viewer, pm, interval, args.once))
    logger.info("Работа завершена.")


if __name__ == "__main__":
    main()

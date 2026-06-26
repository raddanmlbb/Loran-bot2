"""
config.py — конфигурация проекта LORAN.CYBER Bot.

Токен бота и прочие секреты читаются из переменных окружения.
На Bothost.ru переменные задаются через панель управления или .env-файл.
"""

import os
from dataclasses import dataclass, field
from typing import List


@dataclass(frozen=True)
class Config:
    """Иммутабельная конфигурация приложения."""

    # --- Telegram ---
    bot_token: str

    # --- База данных ---
    db_path: str

    # --- Режим интеграции с Senet ---
    # "mock"  — использовать заглушки (текущий этап)
    # "live"  — реальные запросы к Senet API (следующие этапы)
    senet_mode: str

    # --- Web App ---
    webapp_base_url: str

    # --- Администраторы ---
    # Список Telegram ID, которым при первом /start автоматически
    # выдаётся флаг is_admin. Заказчик добавляет свои ID.
    initial_admin_ids: List[int]

    # --- Бизнес-правила ---
    max_pcs_per_booking: int       # максимум ПК в одной брони
    max_booking_days_ahead: int    # максимум дней вперёд для брони
    cancel_before_minutes: int     # отмена не позже чем за N минут


def load_config() -> Config:
    """
    Загружает конфигурацию из переменных окружения.

    Переменные окружения:
        BOT_TOKEN (обязательно): токен бота от @BotFather.
        SENET_MODE: "mock" или "live". По умолчанию "mock".
        DB_PATH: путь к файлу SQLite. По умолчанию "data/loran.db".
        WEBAPP_BASE_URL: базовый URL Web App. По умолчанию — заглушка.
        INITIAL_ADMIN_IDS: Telegram ID через запятую, напр. "123456,789012".

    Returns:
        Config: иммутабельный объект конфигурации.

    Raises:
        ValueError: если BOT_TOKEN не задан.
    """
    bot_token = os.environ.get("BOT_TOKEN", "").strip()
    if not bot_token:
        raise ValueError(
            "BOT_TOKEN не задан. "
            "Установите переменную окружения BOT_TOKEN перед запуском."
        )

    raw_admin_ids = os.environ.get("INITIAL_ADMIN_IDS", "").strip()
    initial_admin_ids: List[int] = []
    if raw_admin_ids:
        for part in raw_admin_ids.split(","):
            part = part.strip()
            if part.isdigit():
                initial_admin_ids.append(int(part))

    return Config(
        bot_token=bot_token,
        db_path=os.environ.get("DB_PATH", "data/loran.db"),
        senet_mode=os.environ.get("SENET_MODE", "mock"),
        webapp_base_url=os.environ.get(
            "WEBAPP_BASE_URL", "https://loran-club.vercel.app"
        ),
        initial_admin_ids=initial_admin_ids,
        max_pcs_per_booking=5,
        max_booking_days_ahead=2,
        cancel_before_minutes=30,
    )

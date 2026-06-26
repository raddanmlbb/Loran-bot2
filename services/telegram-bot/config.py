"""
config.py — конфигурация проекта LORAN.CYBER Bot.
"""

import os
from dataclasses import dataclass, field
from typing import List


@dataclass(frozen=True)
class Config:
    """Иммутабельная конфигурация приложения."""

    # Telegram
    bot_token: str

    # База данных
    db_path: str

    # Режим интеграции с Senet: "mock" или "live"
    senet_mode: str

    # URL Senet API (только для live-режима)
    senet_api_url: str
    senet_api_key: str

    # Web App
    webapp_base_url: str

    # Администраторы — получают is_admin=1 при первом /start
    initial_admin_ids: List[int]

    # Бизнес-правила
    max_pcs_per_booking: int
    max_booking_days_ahead: int
    cancel_before_minutes: int


def load_config() -> Config:
    """
    Загружает конфигурацию из переменных окружения.

    Обязательные переменные:
        BOT_TOKEN — токен бота от @BotFather.

    Опциональные переменные:
        SENET_MODE          — "mock" или "live" (по умолчанию "mock")
        SENET_API_URL       — базовый URL Senet API
        SENET_API_KEY       — ключ приложения Senet (Application Key)
        DB_PATH             — путь к SQLite (по умолчанию "data/loran.db")
        WEBAPP_BASE_URL     — базовый URL Web App
        INITIAL_ADMIN_IDS   — Telegram ID через запятую

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
        senet_api_url=os.environ.get("SENET_API_URL", "https://senet.kz/api"),
        senet_api_key=os.environ.get("SENET_API_KEY", ""),
        webapp_base_url=os.environ.get(
            "WEBAPP_BASE_URL", "https://loran-club.vercel.app"
        ),
        initial_admin_ids=initial_admin_ids,
        max_pcs_per_booking=5,
        max_booking_days_ahead=2,
        cancel_before_minutes=30,
    )

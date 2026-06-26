"""
handlers/commands.py — обработчики команд /start, /help, /profile.

Архитектура:
  - db_path и webapp_base_url передаются через context.bot_data.
  - Все обращения к БД — асинхронные (aiosqlite).
  - Функции построения клавиатур экспортируются для использования в webapp.py.
  - Пользовательский ввод в сообщениях экранируется через html.escape.
"""

import logging
from html import escape
from typing import Optional

import aiosqlite
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    Update,
    WebAppInfo,
)
from telegram.ext import ContextTypes

from db.queries import get_user, set_user_admin, update_last_activity
from locales.ru import MESSAGES

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Построители клавиатур (публичные — используются в webapp.py)
# ---------------------------------------------------------------------------


def _build_guest_menu(webapp_booking_url: str) -> ReplyKeyboardMarkup:
    """
    Строит Reply-клавиатуру для незарегистрированного пользователя.

    Layout:
        [Забронировать]  [Прайс-лист]
        [Адрес]          [Контакты]
        [Зарегистрироваться]

    Args:
        webapp_booking_url: URL Web App. Не используется напрямую в кнопке —
            кнопка «Забронировать» для гостя только текстовая, бот перехватит
            и предложит зарегистрироваться.

    Returns:
        ReplyKeyboardMarkup с кнопками гостя.
    """
    keyboard = [
        [MESSAGES["btn_booking"], MESSAGES["btn_price"]],
        [MESSAGES["btn_address"], MESSAGES["btn_contacts"]],
        [MESSAGES["btn_register"]],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)


def _build_main_menu() -> ReplyKeyboardMarkup:
    """
    Строит Reply-клавиатуру для зарегистрированного пользователя.

    Layout:
        [Забронировать]  [Прайс-лист]
        [Новости]        [Профиль]
        [Адрес]          [Контакты]

    Returns:
        ReplyKeyboardMarkup с основным меню.
    """
    keyboard = [
        [MESSAGES["btn_booking"], MESSAGES["btn_price"]],
        [MESSAGES["btn_news"], MESSAGES["btn_profile"]],
        [MESSAGES["btn_address"], MESSAGES["btn_contacts"]],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)


def _build_booking_inline(webapp_booking_url: str) -> InlineKeyboardMarkup:
    """
    Строит Inline-кнопку «Забронировать», открывающую Web App.

    Args:
        webapp_booking_url: полный URL Web App.

    Returns:
        InlineKeyboardMarkup с кнопкой WebApp.
    """
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            text=MESSAGES["btn_book_now"],
            web_app=WebAppInfo(url=webapp_booking_url),
        )
    ]])


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------


def _booking_url(webapp_base_url: str) -> str:
    """Формирует URL экрана бронирования."""
    return f"{webapp_base_url.rstrip('/')}?screen=booking"


def _price_image_url(webapp_base_url: str) -> str:
    """Формирует URL изображения прайс-листа."""
    return f"{webapp_base_url.rstrip('/')}/assets/price/price-list.jpg"


# ---------------------------------------------------------------------------
# Хендлеры
# ---------------------------------------------------------------------------


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Обрабатывает команду /start.

    Сценарии:
      1. Новый пользователь → приветственное сообщение + гостевое меню.
      2. Забаненный пользователь → сообщение о блокировке.
      3. Активный зарегистрированный → приветствие + основное меню.

    Пользователи из INITIAL_ADMIN_IDS получают is_admin=1 автоматически.

    Args:
        update: апдейт от Telegram.
        context: контекст PTB с bot_data.
    """
    if update.effective_user is None or update.message is None:
        return

    telegram_id: int = update.effective_user.id
    db_path: str = context.bot_data["db_path"]
    webapp_base_url: str = context.bot_data["webapp_base_url"]
    initial_admin_ids: list[int] = context.bot_data.get("initial_admin_ids", [])

    try:
        if telegram_id in initial_admin_ids:
            await set_user_admin(db_path, telegram_id)

        db_user: Optional[aiosqlite.Row] = await get_user(db_path, telegram_id)

        if db_user is None:
            await update.message.reply_text(
                text=MESSAGES["welcome_new"],
                reply_markup=_build_guest_menu(_booking_url(webapp_base_url)),
            )
            logger.info("Новый пользователь: telegram_id=%d", telegram_id)

        elif db_user["status"] == "banned":
            reason: str = escape(db_user["banned_reason"] or "не указана")
            await update.message.reply_text(
                text=MESSAGES["banned"].format(reason=reason),
            )
            logger.info("Попытка входа забаненного: telegram_id=%d", telegram_id)

        else:
            login: str = escape(db_user["login"])
            await update.message.reply_text(
                text=MESSAGES["welcome_back"].format(login=login),
                reply_markup=_build_main_menu(),
            )
            await update_last_activity(db_path, telegram_id)
            logger.info("Пользователь вернулся: telegram_id=%d, login=%s", telegram_id, login)

    except Exception:
        logger.exception("Ошибка в /start для telegram_id=%d", telegram_id)
        await update.message.reply_text(MESSAGES["error_generic"])


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Обрабатывает команду /help.

    Args:
        update: апдейт от Telegram.
        context: контекст PTB.
    """
    if update.message is None:
        return

    try:
        await update.message.reply_text(text=MESSAGES["help_text"])
    except Exception:
        logger.exception(
            "Ошибка в /help для telegram_id=%s",
            update.effective_user.id if update.effective_user else "unknown",
        )
        await update.message.reply_text(MESSAGES["error_generic"])


async def cmd_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Обрабатывает команду /profile.

    Показывает данные профиля: логин, телефон, Senet-логин, статус, дату регистрации.
    Для незарегистрированного пользователя — предложение зарегистрироваться.

    Args:
        update: апдейт от Telegram.
        context: контекст PTB с bot_data.
    """
    if update.effective_user is None or update.message is None:
        return

    telegram_id: int = update.effective_user.id
    db_path: str = context.bot_data["db_path"]

    try:
        db_user: Optional[aiosqlite.Row] = await get_user(db_path, telegram_id)

        if db_user is None:
            await update.message.reply_text(text=MESSAGES["profile_not_registered"])
            return

        # Экранируем все пользовательские данные перед подстановкой
        login = escape(db_user["login"] or "")
        phone = escape(db_user["phone"] or "не указан")
        senet_login = escape(db_user["senet_login"] or "не привязан")
        status_map = {"active": "активен", "banned": "заблокирован"}
        status = status_map.get(db_user["status"] or "active", db_user["status"] or "")

        # Форматируем дату регистрации
        registered_raw: Optional[str] = db_user["registered_at"]
        if registered_raw:
            registered_date = registered_raw[:10]  # YYYY-MM-DD из ISO timestamp
        else:
            registered_date = "неизвестно"

        lines = [
            MESSAGES["profile_header"].format(login=login),
            "",
            MESSAGES["profile_phone"].format(phone=phone),
            MESSAGES["profile_senet"].format(senet_login=senet_login),
            MESSAGES["profile_status"].format(status=status),
            MESSAGES["profile_registered"].format(date=registered_date),
        ]
        await update.message.reply_text(text="\n".join(lines))
        await update_last_activity(db_path, telegram_id)

    except Exception:
        logger.exception("Ошибка в /profile для telegram_id=%d", telegram_id)
        await update.message.reply_text(MESSAGES["error_generic"])

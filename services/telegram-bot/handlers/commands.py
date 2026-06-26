"""
handlers/commands.py — обработчики команд /start, /help, /profile.
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


def _build_guest_menu() -> ReplyKeyboardMarkup:
    keyboard = [
        [MESSAGES["btn_booking"], MESSAGES["btn_price"]],
        [MESSAGES["btn_address"], MESSAGES["btn_contacts"]],
        [MESSAGES["btn_register"]],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)


def _build_main_menu() -> ReplyKeyboardMarkup:
    keyboard = [
        [MESSAGES["btn_booking"], MESSAGES["btn_price"]],
        [MESSAGES["btn_news"], MESSAGES["btn_profile"]],
        [MESSAGES["btn_address"], MESSAGES["btn_contacts"]],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)


def _booking_url(webapp_base_url: str) -> str:
    return f"{webapp_base_url.rstrip('/')}?screen=booking"


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.message is None:
        return

    telegram_id: int = update.effective_user.id
    db_path: str = context.bot_data["db_path"]
    initial_admin_ids: list[int] = context.bot_data.get("initial_admin_ids", [])

    try:
        if telegram_id in initial_admin_ids:
            await set_user_admin(db_path, telegram_id)

        db_user: Optional[aiosqlite.Row] = await get_user(db_path, telegram_id)

        if db_user is None:
            await update.message.reply_text(
                text=MESSAGES["welcome_new"],
                reply_markup=_build_guest_menu(),
            )
            logger.info("Новый пользователь: telegram_id=%d", telegram_id)
        elif db_user["status"] == "banned":
            reason = escape(db_user["banned_reason"] or "не указана")
            await update.message.reply_text(MESSAGES["banned"].format(reason=reason))
        else:
            login = escape(db_user["login"])
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
    if update.message is None:
        return
    try:
        await update.message.reply_text(MESSAGES["help_text"])
    except Exception:
        logger.exception("Ошибка в /help")
        await update.message.reply_text(MESSAGES["error_generic"])


async def cmd_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.message is None:
        return

    telegram_id: int = update.effective_user.id
    db_path: str = context.bot_data["db_path"]

    try:
        db_user = await get_user(db_path, telegram_id)
        if db_user is None:
            await update.message.reply_text(MESSAGES["profile_not_registered"])
            return

        login = escape(db_user["login"] or "")
        phone = escape(db_user["phone"] or "не указан")
        senet_login = escape(db_user["senet_login"] or "не привязан")
        status_map = {"active": "активен", "banned": "заблокирован"}
        status = status_map.get(db_user["status"] or "active", db_user["status"] or "")

        registered_raw: Optional[str] = db_user["registered_at"]
        registered_date = registered_raw[:10] if registered_raw else "неизвестно"

        lines = [
            MESSAGES["profile_header"].format(login=login),
            "",
            MESSAGES["profile_phone"].format(phone=phone),
            MESSAGES["profile_senet"].format(senet_login=senet_login),
            MESSAGES["profile_status"].format(status=status),
            MESSAGES["profile_registered"].format(date=registered_date),
        ]
        await update.message.reply_text("\n".join(lines))
        await update_last_activity(db_path, telegram_id)

    except Exception:
        logger.exception("Ошибка в /profile для telegram_id=%d", telegram_id)
        await update.message.reply_text(MESSAGES["error_generic"])

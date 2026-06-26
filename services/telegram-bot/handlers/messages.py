"""
handlers/messages.py — обработчики текстовых кнопок Reply-клавиатуры.
"""

import logging
from html import escape

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
    WebAppInfo,
)
from telegram.ext import ContextTypes

from db.queries import get_user
from locales.ru import MESSAGES

logger = logging.getLogger(__name__)


def _booking_inline(webapp_booking_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            text=MESSAGES["btn_book_now"],
            web_app=WebAppInfo(url=webapp_booking_url),
        )
    ]])


async def handle_booking(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.message is None:
        return

    telegram_id: int = update.effective_user.id
    db_path: str = context.bot_data["db_path"]
    webapp_base_url: str = context.bot_data["webapp_base_url"]
    booking_url = f"{webapp_base_url.rstrip('/')}?screen=booking"

    try:
        db_user = await get_user(db_path, telegram_id)

        if db_user is None:
            await update.message.reply_text(MESSAGES["error_not_registered"])
            return

        if db_user["status"] == "banned":
            reason = escape(db_user["banned_reason"] or "не указана")
            await update.message.reply_text(MESSAGES["banned"].format(reason=reason))
            return

        if not db_user["senet_verified"]:
            await update.message.reply_text(MESSAGES["error_booking_senet_required"])
            return

        await update.message.reply_text(
            text="Выберите время и компьютеры:",
            reply_markup=_booking_inline(booking_url),
        )
    except Exception:
        logger.exception("Ошибка в handle_booking для telegram_id=%d", telegram_id)
        await update.message.reply_text(MESSAGES["error_generic"])


async def handle_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return

    webapp_base_url: str = context.bot_data["webapp_base_url"]
    price_image_url = f"{webapp_base_url.rstrip('/')}/assets/price/price-list.jpg"
    booking_url = f"{webapp_base_url.rstrip('/')}?screen=booking"

    try:
        await update.message.reply_photo(
            photo=price_image_url,
            caption=MESSAGES["price_caption"],
            reply_markup=_booking_inline(booking_url),
        )
    except Exception:
        logger.exception("Ошибка в handle_price")
        await update.message.reply_text(MESSAGES["price_caption"])


async def handle_address(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    try:
        await update.message.reply_text(MESSAGES["address_text"])
    except Exception:
        logger.exception("Ошибка в handle_address")
        await update.message.reply_text(MESSAGES["error_generic"])


async def handle_contacts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    try:
        # Пробуем загрузить контакты из БД
        from db.queries import get_club_contacts
        db_path: str = context.bot_data["db_path"]
        contacts = await get_club_contacts(db_path)

        if contacts and any([
            contacts["phone"], contacts["whatsapp"],
            contacts["telegram"], contacts["instagram"]
        ]):
            lines = []
            if contacts["instagram"]:
                lines.append(f"Instagram: {contacts['instagram']}")
            if contacts["telegram"]:
                lines.append(f"Telegram: {contacts['telegram']}")
            if contacts["whatsapp"]:
                lines.append(f"WhatsApp: {contacts['whatsapp']}")
            if contacts["phone"]:
                lines.append(f"Телефон: {contacts['phone']}")
            text = "\n".join(lines)
        else:
            text = MESSAGES["contacts_text"]

        await update.message.reply_text(text)
    except Exception:
        logger.exception("Ошибка в handle_contacts")
        await update.message.reply_text(MESSAGES["contacts_text"])


async def handle_news(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    await update.message.reply_text("Новости скоро появятся. Следите за обновлениями!")


async def handle_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from handlers.commands import cmd_profile
    await cmd_profile(update, context)


async def handle_register(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return

    webapp_base_url: str = context.bot_data["webapp_base_url"]
    register_url = f"{webapp_base_url.rstrip('/')}?screen=register"

    await update.message.reply_text(
        text="Открой форму регистрации:",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(
                text=MESSAGES["btn_register"],
                web_app=WebAppInfo(url=register_url),
            )
        ]]),
    )

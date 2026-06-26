"""
handlers/messages.py — обработчики текстовых кнопок Reply-клавиатуры.

Каждая кнопка главного меню — отдельная async-функция.
Маршрутизация по тексту кнопки выполняется в main.py через filters.Text([...]).

Кнопка «Забронировать»:
  - Для зарегистрированного с привязанным Senet → Inline Web App кнопка.
  - Для зарегистрированного без Senet → предложение пройти верификацию.
  - Для гостя → предложение зарегистрироваться.
  - Для забаненного → сообщение о блокировке.
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
    """
    Строит Inline-кнопку Web App бронирования.

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


async def handle_booking(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Обрабатывает нажатие кнопки «Забронировать».

    Сценарии:
      - Гость → предложение зарегистрироваться.
      - Забаненный → сообщение о блокировке.
      - Зарегистрированный без Senet → предложение пройти верификацию.
      - Зарегистрированный с Senet → Inline-кнопка Web App.

    Args:
        update: апдейт от Telegram.
        context: контекст PTB с bot_data.
    """
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

        # Пользователь верифицирован — открываем Web App
        await update.message.reply_text(
            text="Выберите время и компьютеры:",
            reply_markup=_booking_inline(booking_url),
        )

    except Exception:
        logger.exception("Ошибка в handle_booking для telegram_id=%d", telegram_id)
        await update.message.reply_text(MESSAGES["error_generic"])


async def handle_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Обрабатывает нажатие кнопки «Прайс-лист».

    Отправляет изображение прайс-листа с Inline-кнопкой бронирования.
    Доступно всем без регистрации.

    Args:
        update: апдейт от Telegram.
        context: контекст PTB с bot_data.
    """
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
        logger.exception(
            "Ошибка в handle_price для telegram_id=%s",
            update.effective_user.id if update.effective_user else "unknown",
        )
        await update.message.reply_text(MESSAGES["error_generic"])


async def handle_address(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Обрабатывает нажатие кнопки «Адрес».

    Args:
        update: апдейт от Telegram.
        context: контекст PTB.
    """
    if update.message is None:
        return

    try:
        await update.message.reply_text(text=MESSAGES["address_text"])
    except Exception:
        logger.exception(
            "Ошибка в handle_address для telegram_id=%s",
            update.effective_user.id if update.effective_user else "unknown",
        )
        await update.message.reply_text(MESSAGES["error_generic"])


async def handle_contacts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Обрабатывает нажатие кнопки «Контакты».

    Args:
        update: апдейт от Telegram.
        context: контекст PTB.
    """
    if update.message is None:
        return

    try:
        await update.message.reply_text(text=MESSAGES["contacts_text"])
    except Exception:
        logger.exception(
            "Ошибка в handle_contacts для telegram_id=%s",
            update.effective_user.id if update.effective_user else "unknown",
        )
        await update.message.reply_text(MESSAGES["error_generic"])


async def handle_news(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Заглушка обработчика кнопки «Новости».

    Будет реализовано в следующих этапах (показ постов из таблицы posts).

    Args:
        update: апдейт от Telegram.
        context: контекст PTB.
    """
    if update.message is None:
        return

    await update.message.reply_text("Новости скоро появятся. Следите за обновлениями!")


async def handle_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Делегирует обработку кнопки «Профиль» команде /profile.

    Args:
        update: апдейт от Telegram.
        context: контекст PTB.
    """
    # Переиспользуем логику cmd_profile, чтобы не дублировать код
    from handlers.commands import cmd_profile
    await cmd_profile(update, context)


async def handle_register(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Заглушка обработчика кнопки «Зарегистрироваться».

    Регистрация выполняется через Web App (action: "register").
    Эта кнопка открывает Web App на экране регистрации.

    Args:
        update: апдейт от Telegram.
        context: контекст PTB.
    """
    if update.message is None:
        return

    webapp_base_url: str = context.bot_data["webapp_base_url"]
    register_url = f"{webapp_base_url.rstrip('/')}?screen=register"

    from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

    await update.message.reply_text(
        text="Открой форму регистрации:",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(
                text=MESSAGES["btn_register"],
                web_app=WebAppInfo(url=register_url),
            )
        ]]),
    )

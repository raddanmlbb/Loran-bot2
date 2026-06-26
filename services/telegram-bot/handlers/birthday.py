"""
handlers/birthday.py — обработчики для сбора и управления датой рождения. Этап 7.

Callback-кнопки от инлайн-клавиатуры (bday_enter / bday_later / bday_never)
и ConversationHandler для ввода даты.
"""

import logging
import re
from datetime import date
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

logger = logging.getLogger(__name__)

# Состояния
(
    BDAY_AWAIT_DATE,
) = range(1)

# Поддерживаемые форматы дат
_DATE_FORMATS_RE = [
    re.compile(r"^(\d{2})\.(\d{2})\.(\d{4})$"),   # ДД.ММ.ГГГГ
    re.compile(r"^(\d{4})-(\d{2})-(\d{2})$"),       # ГГГГ-ММ-ДД (ISO)
    re.compile(r"^(\d{2})/(\d{2})/(\d{4})$"),        # ДД/ММ/ГГГГ
]

MIN_BIRTH_YEAR: int = 1950
MAX_AGE_YEARS: int = 100


def _parse_birthday(text: str) -> Optional[date]:
    """
    Разбирает строку даты в нескольких форматах.
    Возвращает date или None при ошибке.
    """
    text = text.strip()

    # ДД.ММ.ГГГГ или ДД/ММ/ГГГГ
    for pattern in [_DATE_FORMATS_RE[0], _DATE_FORMATS_RE[2]]:
        m = pattern.match(text)
        if m:
            day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
            try:
                return date(year, month, day)
            except ValueError:
                return None

    # ГГГГ-ММ-ДД
    m = _DATE_FORMATS_RE[1].match(text)
    if m:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return date(year, month, day)
        except ValueError:
            return None

    return None


# ---------------------------------------------------------------------------
# Callback-кнопки: bday_enter, bday_later, bday_never
# ---------------------------------------------------------------------------

async def bday_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Optional[int]:
    """Обрабатывает inline-кнопки запроса дня рождения."""
    query = update.callback_query
    if query is None:
        return None
    await query.answer()

    telegram_id: int = update.effective_user.id
    db_path: str = context.bot_data["db_path"]
    data: str = query.data

    if data == "bday_later":
        # Повторить через 30 дней — просто обновляем asked_at
        from db.queries import update_birthday_asked
        await update_birthday_asked(db_path, telegram_id)
        await query.edit_message_text(
            "Хорошо, спросим позже! 😊\n"
            "Ты всегда можешь указать дату рождения в своём профиле (/profile)."
        )
        return None

    elif data == "bday_never":
        # Больше не спрашивать
        from db.queries import set_birthday_declined
        await set_birthday_declined(db_path, telegram_id, forever=True)
        await query.edit_message_text(
            "Понял, больше не будем спрашивать! 👍"
        )
        return None

    elif data == "bday_enter":
        await query.edit_message_text(
            "🎂 <b>Укажи дату рождения</b>\n\n"
            "Введи дату в формате <code>ДД.ММ.ГГГГ</code>\n"
            "Например: <code>15.03.1998</code>\n\n"
            "Или /cancel чтобы пропустить.",
            parse_mode="HTML",
        )
        context.user_data["bday_from_callback"] = True
        return BDAY_AWAIT_DATE

    return None


# ---------------------------------------------------------------------------
# ConversationHandler: ввод и сохранение даты рождения
# ---------------------------------------------------------------------------

async def bday_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Команда /birthday — прямой вход в диалог указания ДР."""
    if update.message is None:
        return ConversationHandler.END
    await update.message.reply_text(
        "🎂 <b>Укажи дату рождения</b>\n\n"
        "Введи дату в формате <code>ДД.ММ.ГГГГ</code>\n"
        "Например: <code>15.03.1998</code>",
        parse_mode="HTML",
    )
    return BDAY_AWAIT_DATE


async def bday_receive_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.effective_user is None:
        return BDAY_AWAIT_DATE

    telegram_id: int = update.effective_user.id
    db_path: str = context.bot_data["db_path"]
    text = update.message.text.strip()

    bday = _parse_birthday(text)
    if bday is None:
        await update.message.reply_text(
            "❌ Не удалось распознать дату.\n"
            "Введи в формате <code>ДД.ММ.ГГГГ</code>, например <code>15.03.1998</code>:",
            parse_mode="HTML",
        )
        return BDAY_AWAIT_DATE

    today = date.today()
    if bday.year < MIN_BIRTH_YEAR or bday > today:
        await update.message.reply_text(
            "❌ Неверная дата. Укажи настоящую дату рождения:"
        )
        return BDAY_AWAIT_DATE

    age = (today - bday).days // 365
    if age > MAX_AGE_YEARS:
        await update.message.reply_text(
            "❌ Дата не похожа на настоящую. Попробуй ещё раз:"
        )
        return BDAY_AWAIT_DATE

    from services.birthday import save_birthday
    await save_birthday(db_path, telegram_id, bday)

    day_month = bday.strftime("%d %B").lstrip("0")
    await update.message.reply_text(
        f"✅ Отлично! Дата рождения сохранена: {bday.strftime('%d.%m.%Y')}\n\n"
        f"🎉 В твой день рождения ({day_month}) мы пришлём промокод "
        f"на скидку 20%!"
    )
    context.user_data.pop("bday_from_callback", None)
    return ConversationHandler.END


async def bday_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message:
        await update.message.reply_text("Ладно, в следующий раз! 😊")
    context.user_data.pop("bday_from_callback", None)
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# Сборка хендлеров
# ---------------------------------------------------------------------------

def build_birthday_handler() -> ConversationHandler:
    """ConversationHandler для ввода даты рождения через команду /birthday."""
    return ConversationHandler(
        entry_points=[
            CommandHandler("birthday", bday_start),
            CallbackQueryHandler(bday_callback, pattern=r"^bday_enter$"),
        ],
        states={
            BDAY_AWAIT_DATE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, bday_receive_date),
            ],
        },
        fallbacks=[CommandHandler("cancel", bday_cancel)],
        per_user=True,
        per_chat=True,
        per_message=False,
    )


def build_bday_inline_handler() -> CallbackQueryHandler:
    """Отдельный хендлер для bday_later и bday_never (не входят в ConversationHandler)."""
    return CallbackQueryHandler(bday_callback, pattern=r"^bday_(later|never)$")

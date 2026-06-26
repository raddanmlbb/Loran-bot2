"""
services/birthday.py — логика дней рождения LORAN.CYBER. Этап 7.

Функции:
  - check_and_ask_birthday: предложить указать ДР после 3-й брони
  - run_birthday_greetings: ежедневная проверка + поздравление
  - save_birthday: сохранить дату рождения пользователя
"""

import logging
import secrets
import string
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import Forbidden, TelegramError

logger = logging.getLogger(__name__)

BIRTHDAY_DISCOUNT: int = 20
PROMO_VALID_DAYS: int = 7
PROMO_ALPHABET: str = string.ascii_uppercase + string.digits
PROMO_SUFFIX_LEN: int = 6

# Повторный запрос через 30 дней после "не сейчас"
ASK_COOLDOWN_DAYS: int = 30
# Спрашивать при кратных бронях: 3, 10, 20 ...
ASK_AFTER_BOOKINGS: int = 3


def _generate_promo_code(telegram_id: int) -> str:
    suffix = "".join(secrets.choice(PROMO_ALPHABET) for _ in range(PROMO_SUFFIX_LEN))
    short_id = str(telegram_id)[-3:]
    return f"BDAY-{short_id}-{suffix[:4]}"


def _build_ask_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎂 Указать дату рождения", callback_data="bday_enter")],
        [InlineKeyboardButton("Не сейчас", callback_data="bday_later"),
         InlineKeyboardButton("Не спрашивать", callback_data="bday_never")],
    ])


async def check_and_ask_birthday(
    bot: Bot,
    db_path: str,
    telegram_id: int,
) -> None:
    """
    Проверяет, нужно ли предложить пользователю указать ДР.
    Вызывается после каждого успешного бронирования.
    """
    from db.queries import get_user, count_confirmed_bookings

    user = await get_user(db_path, telegram_id)
    if user is None:
        return

    # Если явно отказался — не предлагать никогда
    if user["birthday_declined"] == 2:
        return

    # Если уже указан ДР
    if user["birthday"]:
        return

    # Количество завершённых броней
    total = await count_confirmed_bookings(db_path, telegram_id)
    if total < ASK_AFTER_BOOKINGS:
        return

    # Проверяем кулдаун (30 дней после "не сейчас")
    asked_at: Optional[str] = user["birthday_asked_at"]
    if asked_at:
        try:
            last_asked = date.fromisoformat(asked_at)
            if (date.today() - last_asked).days < ASK_COOLDOWN_DAYS:
                return
        except ValueError:
            pass

    # Спрашиваем только при кратных бронях: 3, 10, 20 ...
    if total == ASK_AFTER_BOOKINGS or total % 10 == 0:
        try:
            await bot.send_message(
                chat_id=telegram_id,
                text=(
                    "🎂 <b>Хочешь получать подарок на день рождения?</b>\n\n"
                    "Укажи дату — и в твой день рождения LORAN.CYBER пришлёт промокод "
                    f"на скидку {BIRTHDAY_DISCOUNT}%!\n\n"
                    "Это не обязательно — ты всегда можешь пропустить."
                ),
                parse_mode="HTML",
                reply_markup=_build_ask_keyboard(),
            )
            # Обновляем дату последнего запроса
            from db.queries import update_birthday_asked
            await update_birthday_asked(db_path, telegram_id)
        except (Forbidden, TelegramError) as exc:
            logger.debug("Не удалось отправить запрос ДР для tid=%d: %s", telegram_id, exc)


async def save_birthday(db_path: str, telegram_id: int, bday: date, source: str = "user") -> None:
    """Сохраняет дату рождения пользователя."""
    from db.queries import set_birthday
    await set_birthday(db_path, telegram_id, bday.isoformat(), source)


async def run_birthday_greetings(bot: Bot, db_path: str) -> None:
    """
    Ежедневная задача: ищет именинников сегодня и отправляет поздравление
    с персональным промокодом. Один бонус в год на человека.
    """
    from db.queries import get_todays_birthdays, set_last_birthday_bonus
    from db.queries_posts import _db

    today = date.today()
    year = today.year

    users = await get_todays_birthdays(db_path)
    logger.info("Именинников сегодня (%s): %d", today.isoformat(), len(users))

    for user in users:
        tid: int = user["telegram_id"]
        login: str = user["login"] or str(tid)

        # Один промокод в год
        last_bonus: Optional[str] = user["last_birthday_bonus"]
        if last_bonus:
            try:
                if date.fromisoformat(last_bonus).year == year:
                    logger.debug("tid=%d уже получил бонус в %d году", tid, year)
                    continue
            except ValueError:
                pass

        # Генерируем промокод
        code = _generate_promo_code(tid)
        expires_at = today + timedelta(days=PROMO_VALID_DAYS)

        # Сохраняем в birthday_promos
        async with _db(db_path) as conn:
            try:
                await conn.execute(
                    """
                    INSERT INTO birthday_promos (telegram_id, code, year, discount, expires_at)
                    VALUES (?, ?, ?, ?, ?);
                    """,
                    (tid, code, year, BIRTHDAY_DISCOUNT, expires_at.isoformat()),
                )
                await conn.commit()
            except Exception as exc:
                logger.warning("Не удалось сохранить promo для tid=%d: %s", tid, exc)
                continue

        # Отправляем поздравление
        try:
            await bot.send_message(
                chat_id=tid,
                text=(
                    f"🎉 <b>С днём рождения, {login}!</b>\n\n"
                    f"Команда LORAN.CYBER дарит тебе скидку {BIRTHDAY_DISCOUNT}% "
                    f"на любую бронь.\n\n"
                    f"🎟 Промокод: <code>{code}</code>\n"
                    f"⏳ Действует {PROMO_VALID_DAYS} дней (до {expires_at.strftime('%d.%m.%Y')}).\n\n"
                    "Ждём тебя в клубе! 🖥"
                ),
                parse_mode="HTML",
            )
            await set_last_birthday_bonus(db_path, tid, today.isoformat())
            logger.info("Поздравление отправлено tid=%d, code=%s", tid, code)

        except Forbidden:
            logger.debug("tid=%d заблокировал бота, пропускаем.", tid)
        except TelegramError as exc:
            logger.warning("Ошибка отправки поздравления tid=%d: %s", tid, exc)

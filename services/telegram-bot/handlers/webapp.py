"""
handlers/webapp.py — обработчик данных от Telegram Web App.

Этап 5: регистрация принимает поле password и передаёт его в Senet API.
"""

import json
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Optional

from telegram import Update
from telegram.ext import ContextTypes

from db.queries import (
    clear_pending_registration,
    create_user,
    get_pending_registration,
    get_user,
    get_user_by_login,
    get_user_by_senet_login,
    increment_senet_verify_attempts,
    link_senet_user,
    lock_senet_verification,
    mark_verification_code_used,
    reset_senet_verify_attempts,
    save_pending_registration,
    save_verification_code,
    update_last_activity,
)
from locales.ru import MESSAGES
from senet_api import SenetAPI, SenetAPIError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Константы валидации логина
# ---------------------------------------------------------------------------
LOGIN_MIN_LEN: int = 3
LOGIN_MAX_LEN: int = 16
LOGIN_RE: re.Pattern[str] = re.compile(r"^[a-zA-Z0-9_]+$")

STOP_LIST: frozenset[str] = frozenset({
    "admin", "moder", "moderator", "loran", "loransupport",
    "bot", "support", "root", "owner", "staff", "system",
    "administrator", "superadmin", "manager", "director",
})

PROFANITY_LIST: frozenset[str] = frozenset({
    "хуй", "пизд", "еба", "ebal", "fuck", "shit", "bitch",
    "сука", "blyad", "бля", "наху", "залуп", "член",
})

# Константы верификации
CODE_ALPHABET: str = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_SUFFIX_LEN: int = 4
CODE_PREFIX: str = "LOR-VRFY-"
CODE_TTL_SECONDS: int = 600

MAX_VERIFY_ATTEMPTS: int = 3
LOCK_DURATION_MINUTES: int = 30
PENDING_REGISTRATION_TTL_MINUTES: int = 30

# Формат телефона (Казахстан)
_KZ_PHONE_RE: re.Pattern[str] = re.compile(r"^(?:\+7|8|7)([0-9]{10})$")

# Требования к паролю
PASSWORD_MIN_LEN: int = 6
_PASSWORD_HAS_LETTER = re.compile(r"[a-zA-Zа-яёА-ЯЁ]")
_PASSWORD_HAS_DIGIT = re.compile(r"\d")


def validate_kz_phone(raw: str) -> Optional[str]:
    cleaned = re.sub(r"[\s\-()]", "", raw.strip())
    match = _KZ_PHONE_RE.match(cleaned)
    if not match:
        return None
    digits = match.group(1)
    return f"+7{digits}"


def validate_login(login: str) -> tuple[bool, Optional[str]]:
    if not (LOGIN_MIN_LEN <= len(login) <= LOGIN_MAX_LEN):
        return False, MESSAGES["error_login_format"]
    if not LOGIN_RE.match(login):
        return False, MESSAGES["error_login_format"]

    login_lower = login.lower()
    for blocked in STOP_LIST:
        if blocked in login_lower:
            return False, MESSAGES["error_login_banned"]
    for profanity in PROFANITY_LIST:
        if profanity in login_lower:
            return False, MESSAGES["error_login_banned"]

    return True, None


def validate_password(password: str) -> tuple[bool, Optional[str]]:
    """
    Проверяет пароль: минимум 6 символов, должны быть буквы и цифры.

    Returns:
        (True, None) если пароль допустим.
        (False, "сообщение") если пароль отклонён.
    """
    if len(password) < PASSWORD_MIN_LEN:
        return False, MESSAGES["password_invalid"]
    if not _PASSWORD_HAS_LETTER.search(password):
        return False, MESSAGES["password_invalid"]
    if not _PASSWORD_HAS_DIGIT.search(password):
        return False, MESSAGES["password_invalid"]
    return True, None


def generate_verification_code() -> str:
    suffix = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_SUFFIX_LEN))
    return f"{CODE_PREFIX}{suffix}"


async def _check_verification_lock(
    db_path: str,
    telegram_id: int,
) -> tuple[bool, Optional[str]]:
    user = await get_user(db_path, telegram_id)
    if user is None:
        return True, None

    locked_until_raw: Optional[str] = user["senet_verify_locked_until"]
    if locked_until_raw:
        try:
            locked_until = datetime.fromisoformat(locked_until_raw).replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) < locked_until:
                return False, MESSAGES["verification_blocked"]
        except ValueError:
            logger.warning("Некорректное senet_verify_locked_until для telegram_id=%d", telegram_id)

    return True, None


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------

async def handle_webapp_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.message is None:
        return
    if update.message.web_app_data is None:
        return

    telegram_id: int = update.effective_user.id

    try:
        raw_data: str = update.message.web_app_data.data
        if len(raw_data.encode("utf-8")) > 10_240:
            logger.warning("WebApp пейлоад превышает 10 КБ от telegram_id=%d", telegram_id)
            await update.message.reply_text(MESSAGES["error_invalid_data"])
            return

        data: dict = json.loads(raw_data)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Некорректный JSON от Web App, telegram_id=%d", telegram_id)
        await update.message.reply_text(MESSAGES["error_invalid_data"])
        return

    if not isinstance(data, dict):
        await update.message.reply_text(MESSAGES["error_invalid_data"])
        return

    action: str = str(data.get("action", "")).strip()
    logger.info("WebApp action=%r от telegram_id=%d", action, telegram_id)

    if action == "register":
        await _handle_register(update, context, data)
    elif action == "verify_senet":
        await _handle_verify_senet(update, context, data)
    elif action == "request_verification_code":
        await _handle_request_code(update, context, data)
    elif action == "booking":
        await _handle_booking(update, context, data)
    elif action == "cancel_booking":
        await _handle_cancel_booking(update, context, data)
    else:
        logger.warning("Неизвестный action=%r от telegram_id=%d", action, telegram_id)
        await update.message.reply_text(MESSAGES["error_unknown_action"])


# ---------------------------------------------------------------------------
# action: "register"  (Этап 5: добавлено поле password)
# ---------------------------------------------------------------------------

async def _handle_register(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    data: dict,
) -> None:
    if update.effective_user is None or update.message is None:
        return

    telegram_id: int = update.effective_user.id
    db_path: str = context.bot_data["db_path"]
    senet: SenetAPI = context.bot_data["senet"]

    login: str = str(data.get("login", "")).strip()
    display_name: Optional[str] = str(data.get("display_name", "")).strip() or None
    raw_phone: str = str(data.get("phone", "")).strip()
    raw_password: str = str(data.get("password", "")).strip()

    try:
        # Уже зарегистрирован?
        existing_user = await get_user(db_path, telegram_id)
        if existing_user is not None:
            await update.message.reply_text(
                MESSAGES["error_already_registered"].format(login=escape(existing_user["login"]))
            )
            return

        # Валидация логина
        ok, error_msg = validate_login(login)
        if not ok:
            await update.message.reply_text(error_msg)
            return

        # Телефон обязателен
        if not raw_phone:
            await update.message.reply_text(MESSAGES["phone_required"])
            return

        phone: Optional[str] = validate_kz_phone(raw_phone)
        if phone is None:
            await update.message.reply_text(MESSAGES["phone_invalid_format"])
            return

        # Пароль обязателен (Этап 5)
        if not raw_password:
            await update.message.reply_text(MESSAGES["password_required"])
            return

        ok_pwd, pwd_error = validate_password(raw_password)
        if not ok_pwd:
            await update.message.reply_text(pwd_error)
            return

        # Логин занят?
        taken = await get_user_by_login(db_path, login)
        if taken is not None:
            await update.message.reply_text(MESSAGES["error_login_taken"])
            return

        # Логин найден в Senet?
        senet_user_exists = senet.user_exists(login)

        if senet_user_exists:
            # Сохраняем pending, отправляем на верификацию
            expires = datetime.now(timezone.utc) + timedelta(minutes=PENDING_REGISTRATION_TTL_MINUTES)
            await save_pending_registration(db_path, telegram_id, login, phone, display_name, expires)

            can_verify, lock_msg = await _check_verification_lock(db_path, telegram_id)
            if not can_verify:
                await update.message.reply_text(lock_msg)
                return

            await update.message.reply_text(
                MESSAGES["verification_required"].format(login=escape(login))
            )
        else:
            # Новый логин — создаём в Senet (с паролем) и в локальной БД
            try:
                senet_user = senet.create_user(login=login, password=raw_password, phone=phone)
            except SenetAPIError as exc:
                logger.error("Ошибка создания пользователя Senet: %s", exc)
                await update.message.reply_text(MESSAGES["error_generic"])
                return

            await create_user(
                db_path=db_path,
                telegram_id=telegram_id,
                login=login,
                display_name=display_name,
                phone=phone,
            )
            await link_senet_user(db_path, telegram_id, senet_user.user_id, senet_user.login)
            await update.message.reply_text(
                MESSAGES["registered_success"].format(login=escape(login))
            )

    except Exception:
        logger.exception("Ошибка в _handle_register для telegram_id=%d", telegram_id)
        await update.message.reply_text(MESSAGES["error_generic"])


# ---------------------------------------------------------------------------
# action: "verify_senet"
# ---------------------------------------------------------------------------

async def _handle_verify_senet(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    data: dict,
) -> None:
    if update.effective_user is None or update.message is None:
        return

    telegram_id: int = update.effective_user.id
    db_path: str = context.bot_data["db_path"]
    senet: SenetAPI = context.bot_data["senet"]

    phone_last4: str = str(data.get("phone_last4", "")).strip()

    try:
        can_verify, lock_msg = await _check_verification_lock(db_path, telegram_id)
        if not can_verify:
            await update.message.reply_text(lock_msg)
            return

        pending = await get_pending_registration(db_path, telegram_id)
        if pending is None:
            await update.message.reply_text(MESSAGES["error_not_registered"])
            return

        senet_login = pending["login"]
        if not senet.verify_phone_last4(senet_login, phone_last4):
            attempts = await increment_senet_verify_attempts(db_path, telegram_id)
            remaining = MAX_VERIFY_ATTEMPTS - attempts
            if remaining <= 0:
                locked_until = datetime.now(timezone.utc) + timedelta(minutes=LOCK_DURATION_MINUTES)
                await lock_senet_verification(db_path, telegram_id, locked_until)
                await update.message.reply_text(MESSAGES["verification_blocked"])
            else:
                await update.message.reply_text(
                    MESSAGES["verification_failed"].format(attempts=remaining)
                )
            return

        senet_user = senet.get_user(senet_login)
        if senet_user is None:
            await update.message.reply_text(MESSAGES["error_generic"])
            return

        # Привязан к другому?
        already_linked = await get_user_by_senet_login(db_path, senet_login)
        if already_linked is not None and already_linked["telegram_id"] != telegram_id:
            await update.message.reply_text(MESSAGES["error_senet_linked"])
            return

        await create_user(
            db_path=db_path,
            telegram_id=telegram_id,
            login=pending["login"],
            display_name=pending["display_name"],
            phone=pending["phone"],
        )
        await link_senet_user(db_path, telegram_id, senet_user.user_id, senet_user.login)
        await reset_senet_verify_attempts(db_path, telegram_id)
        await clear_pending_registration(db_path, telegram_id)

        await update.message.reply_text(
            MESSAGES["verification_success"].format(login=escape(senet_login))
        )

    except Exception:
        logger.exception("Ошибка в _handle_verify_senet для telegram_id=%d", telegram_id)
        await update.message.reply_text(MESSAGES["error_generic"])


# ---------------------------------------------------------------------------
# action: "request_verification_code"
# ---------------------------------------------------------------------------

async def _handle_request_code(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    data: dict,
) -> None:
    if update.effective_user is None or update.message is None:
        return

    telegram_id: int = update.effective_user.id
    db_path: str = context.bot_data["db_path"]

    try:
        user = await get_user(db_path, telegram_id)
        if user is None or user["status"] != "active":
            await update.message.reply_text(MESSAGES["error_not_registered"])
            return

        if user["senet_verified"]:
            await update.message.reply_text(
                MESSAGES["registered_with_senet"].format(login=escape(user["login"]))
            )
            return

        code = generate_verification_code()
        expires = datetime.now(timezone.utc) + timedelta(seconds=CODE_TTL_SECONDS)
        await save_verification_code(db_path, code, telegram_id, user["login"], expires)

        await update.message.reply_text(
            MESSAGES["verification_code_sent"].format(
                code=code,
                minutes=CODE_TTL_SECONDS // 60,
            )
        )

    except Exception:
        logger.exception("Ошибка в _handle_request_code для telegram_id=%d", telegram_id)
        await update.message.reply_text(MESSAGES["error_generic"])


# ---------------------------------------------------------------------------
# action: "booking"
# ---------------------------------------------------------------------------

async def _handle_booking(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    data: dict,
) -> None:
    if update.effective_user is None or update.message is None:
        return

    telegram_id: int = update.effective_user.id
    db_path: str = context.bot_data["db_path"]

    from services.booking import create_booking
    result, error = await create_booking(db_path, telegram_id, data)

    if error:
        await update.message.reply_text(error)
        return

    await update.message.reply_text(
        MESSAGES["booking_success"].format(
            code=result.booking_code,
            date=result.date,
            time_from=result.time_from,
            time_to=result.time_to,
            pc_list=result.pc_list,
            total_price=result.total_price,
        )
    )
    await update_last_activity(db_path, telegram_id)


# ---------------------------------------------------------------------------
# action: "cancel_booking"
# ---------------------------------------------------------------------------

async def _handle_cancel_booking(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    data: dict,
) -> None:
    if update.effective_user is None or update.message is None:
        return

    telegram_id: int = update.effective_user.id
    db_path: str = context.bot_data["db_path"]
    booking_code: str = str(data.get("booking_code", "")).strip()

    from services.booking import cancel_booking
    result, error = await cancel_booking(db_path, telegram_id, booking_code)

    if error:
        await update.message.reply_text(error)
        return

    await update.message.reply_text(
        MESSAGES["booking_cancelled"].format(code=result.booking_code)
    )

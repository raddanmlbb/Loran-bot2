"""
handlers/webapp.py — обработчик данных от Telegram Web App.

Архитектура:
  handle_webapp_data() — точка входа, роутер по полю action.
  Каждое действие изолировано в отдельной async-функции.

Порядок обработки каждого действия:
  1. Извлечь и очистить поля из JSON.
  2. Валидировать типы, форматы, диапазоны.
  3. Проверить права пользователя в БД.
  4. Выполнить бизнес-логику.
  5. Ответить пользователю через reply_text.

Безопасность:
  - telegram_id берётся ТОЛЬКО из update.effective_user.id.
  - Все строки из пейлоада экранируются через html.escape перед выводом.
  - Все SQL-параметры через плейсхолдеры ? (в db/queries.py).
  - Цена брони пересчитывается на сервере, значение из пейлоада игнорируется.
  - Лимиты проверяются до обращения к Senet API.
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

# ---------------------------------------------------------------------------
# Константы верификации
# ---------------------------------------------------------------------------

CODE_ALPHABET: str = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_SUFFIX_LEN: int = 4
CODE_PREFIX: str = "LOR-VRFY-"
CODE_TTL_SECONDS: int = 600          # 10 минут

MAX_VERIFY_ATTEMPTS: int = 3
LOCK_DURATION_MINUTES: int = 30

# TTL ожидания верификации Senet (хранится в pending_registrations)
PENDING_REGISTRATION_TTL_MINUTES: int = 30

# ---------------------------------------------------------------------------
# Формат телефона (Казахстан)
# ---------------------------------------------------------------------------

# Принимаем: +7XXXXXXXXXX, 8XXXXXXXXXX, 7XXXXXXXXXX (все KZ-номера)
_KZ_PHONE_RE: re.Pattern[str] = re.compile(
    r"^(?:\+7|8|7)([0-9]{10})$"
)


def validate_kz_phone(raw: str) -> Optional[str]:
    """
    Валидирует и нормализует казахстанский номер телефона.

    Принимаемые форматы: +7XXXXXXXXXX, 8XXXXXXXXXX, 7XXXXXXXXXX.
    Пробелы, дефисы и скобки удаляются перед проверкой.

    Args:
        raw: сырая строка номера телефона от пользователя.

    Returns:
        Нормализованный номер в формате +7XXXXXXXXXX или None, если формат неверен.

    Example:
        validate_kz_phone("+77011234567")  # "+77011234567"
        validate_kz_phone("87011234567")   # "+77011234567"
        validate_kz_phone("7701 123 45 67") # "+77011234567"
        validate_kz_phone("+1234567890")   # None
    """
    # Убираем пробелы, дефисы, скобки
    cleaned = re.sub(r"[\s\-()]", "", raw.strip())
    match = _KZ_PHONE_RE.match(cleaned)
    if not match:
        return None
    digits = match.group(1)
    return f"+7{digits}"


# ---------------------------------------------------------------------------
# Валидация логина
# ---------------------------------------------------------------------------


def validate_login(login: str) -> tuple[bool, Optional[str]]:
    """
    Проверяет логин по всем правилам клуба.

    Порядок проверок: длина → символы → стоп-лист → мат.

    Args:
        login: сырой логин от пользователя.

    Returns:
        (True, None) если логин допустим.
        (False, "текст ошибки") если логин отклонён.
    """
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


# ---------------------------------------------------------------------------
# Генерация кода верификации
# ---------------------------------------------------------------------------


def generate_verification_code() -> str:
    """
    Генерирует криптографически стойкий код для стойки.

    Формат: LOR-VRFY-XXXX, где X из CODE_ALPHABET (без похожих символов O/0/I/1/L).

    Returns:
        str: код вида "LOR-VRFY-AB3C".
    """
    suffix = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_SUFFIX_LEN))
    return f"{CODE_PREFIX}{suffix}"


# ---------------------------------------------------------------------------
# Проверка блокировки верификации
# ---------------------------------------------------------------------------


async def _check_verification_lock(
    db_path: str,
    telegram_id: int,
) -> tuple[bool, Optional[str]]:
    """
    Проверяет, не заблокирован ли пользователь по верификации.

    Args:
        db_path: путь к файлу БД.
        telegram_id: Telegram ID пользователя.

    Returns:
        (True, None) если можно продолжать.
        (False, "сообщение") если заблокирован.
    """
    user = await get_user(db_path, telegram_id)
    if user is None:
        return True, None

    locked_until_raw: Optional[str] = user["senet_verify_locked_until"]
    if locked_until_raw:
        try:
            locked_until = datetime.fromisoformat(locked_until_raw).replace(
                tzinfo=timezone.utc
            )
            if datetime.now(timezone.utc) < locked_until:
                return False, MESSAGES["verification_blocked"]
        except ValueError:
            logger.warning(
                "Некорректное значение senet_verify_locked_until для telegram_id=%d: %r",
                telegram_id, locked_until_raw,
            )

    return True, None


# ---------------------------------------------------------------------------
# Роутер
# ---------------------------------------------------------------------------


async def handle_webapp_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Точка входа для данных от Telegram Web App.

    Маршрутизирует по полю action к конкретному обработчику.

    Args:
        update: апдейт от Telegram.
        context: контекст PTB с bot_data.
    """
    if update.effective_user is None or update.message is None:
        return
    if update.message.web_app_data is None:
        return

    telegram_id: int = update.effective_user.id

    try:
        raw_data: str = update.message.web_app_data.data
        # Ограничение размера пейлоада: 10 КБ
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
# action: "register"
# ---------------------------------------------------------------------------


async def _handle_register(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    data: dict,
) -> None:
    """
    Обрабатывает action="register" от Web App.

    Сценарии:
      A. Telegram ID уже зарегистрирован → сообщение.
      B. Логин не проходит валидацию → ошибка.
      C. Телефон отсутствует или неверный формат → ошибка.
      D. Логин уже занят в БД → ошибка.
      E. Логин найден в Senet → сохранить pending_registration, запросить верификацию.
      F. Логин новый → создать в Senet + в БД → успех.

    Args:
        update: апдейт от Telegram.
        context: контекст PTB.
        data: распарсенный JSON от Web App.
    """
    if update.effective_user is None or update.message is None:
        return

    telegram_id: int = update.effective_user.id
    db_path: str = context.bot_data["db_path"]
    senet: SenetAPI = context.bot_data["senet"]

    # Извлекаем и нормализуем поля
    login: str = str(data.get("login", "")).strip()
    display_name: Optional[str] = str(data.get("display_name", "")).strip() or None
    raw_phone: str = str(data.get("phone", "")).strip()

    try:
        # A. Уже зарегистрирован?
        existing_user = await get_user(db_path, telegram_id)
        if existing_user is not None:
            await update.message.reply_text(
                MESSAGES["error_already_registered"].format(
                    login=escape(existing_user["login"])
                )
            )
            return

        # B. Валидация логина
        ok, error_msg = validate_login(login)
        if not ok:
            await update.message.reply_text(error_msg)
            return

        # C. Телефон обязателен
        if not raw_phone:
            await update.message.reply_text(MESSAGES["phone_required"])
            return

        phone: Optional[str] = validate_kz_phone(raw_phone)
        if phone is None:
            await update.message.reply_text(MESSAGES["phone_invalid_format"])
            return

        # D. Логин занят в нашей БД?
        login_owner = await get_user_by_login(db_path, login)
        if login_owner is not None:
            await update.message.reply_text(MESSAGES["error_login_taken"])
            return

        # E/F. Проверяем Senet
        senet_exists: bool = senet.user_exists(login)

        if senet_exists:
            # E. Логин есть в Senet — сохранить данные, запросить верификацию по телефону.
            # Пользователя в users пока НЕ создаём.
            pending_expires = datetime.now(timezone.utc) + timedelta(
                minutes=PENDING_REGISTRATION_TTL_MINUTES
            )
            await save_pending_registration(
                db_path=db_path,
                telegram_id=telegram_id,
                login=login,
                phone=phone,
                display_name=display_name,
                expires_at=pending_expires,
            )
            await update.message.reply_text(
                MESSAGES["verification_required"].format(login=escape(login))
            )
            logger.info(
                "Логин %r найден в Senet, pending сохранён для telegram_id=%d",
                login, telegram_id,
            )

        else:
            # F. Новый пользователь — создаём в Senet и в БД.
            try:
                senet_user = senet.create_user(login, phone)
            except SenetAPIError as exc:
                logger.error("Ошибка Senet create_user(%r): %s", login, exc)
                await update.message.reply_text(MESSAGES["error_generic"])
                return

            await create_user(
                db_path=db_path,
                telegram_id=telegram_id,
                login=login,
                display_name=display_name,
                phone=phone,
            )
            await link_senet_user(
                db_path=db_path,
                telegram_id=telegram_id,
                senet_user_id=senet_user.user_id,
                senet_login=login,
            )
            await update_last_activity(db_path, telegram_id)

            from handlers.commands import _build_main_menu

            await update.message.reply_text(
                MESSAGES["registered_success"].format(login=escape(login)),
                reply_markup=_build_main_menu(),
            )
            logger.info(
                "Новый пользователь: telegram_id=%d, login=%r",
                telegram_id, login,
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
    """
    Обрабатывает action="verify_senet" от Web App.

    Пользователь вводит последние 4 цифры телефона для подтверждения
    владения Senet-аккаунтом.

    Лимиты:
      - 3 неудачные попытки → блокировка на 30 минут.
      - 1 Senet-аккаунт → только 1 telegram_id.

    Args:
        update: апдейт от Telegram.
        context: контекст PTB.
        data: распарсенный JSON от Web App.
    """
    if update.effective_user is None or update.message is None:
        return

    telegram_id: int = update.effective_user.id
    db_path: str = context.bot_data["db_path"]
    senet: SenetAPI = context.bot_data["senet"]

    login: str = str(data.get("login", "")).strip()
    phone_last4: str = str(data.get("phone_last4", "")).strip()

    try:
        # Базовая валидация
        if not login or not (phone_last4.isdigit() and len(phone_last4) == 4):
            await update.message.reply_text(MESSAGES["error_invalid_data"])
            return

        # Проверяем блокировку
        can_proceed, lock_msg = await _check_verification_lock(db_path, telegram_id)
        if not can_proceed:
            await update.message.reply_text(lock_msg)
            return

        # Проверяем: не привязан ли Senet к другому telegram_id
        existing_owner = await get_user_by_senet_login(db_path, login)
        if existing_owner is not None and existing_owner["telegram_id"] != telegram_id:
            await update.message.reply_text(MESSAGES["error_senet_linked"])
            return

        # Проверяем через Senet API
        try:
            phone_matches: bool = senet.verify_phone_last4(login, phone_last4)
        except (SenetAPIError, ValueError) as exc:
            logger.error("Ошибка Senet verify_phone_last4(%r): %s", login, exc)
            await update.message.reply_text(MESSAGES["error_generic"])
            return

        if not phone_matches:
            attempts = await increment_senet_verify_attempts(db_path, telegram_id)
            attempts_left = max(0, MAX_VERIFY_ATTEMPTS - attempts)

            if attempts >= MAX_VERIFY_ATTEMPTS:
                locked_until = datetime.now(timezone.utc) + timedelta(
                    minutes=LOCK_DURATION_MINUTES
                )
                await lock_senet_verification(db_path, telegram_id, locked_until)
                await update.message.reply_text(MESSAGES["verification_blocked"])
                logger.warning(
                    "Верификация заблокирована для telegram_id=%d после %d попыток",
                    telegram_id, attempts,
                )
            else:
                await update.message.reply_text(
                    MESSAGES["verification_failed"].format(attempts=attempts_left)
                )
            return

        # Верификация успешна
        # Берём данные из pending_registrations, если есть
        pending = await get_pending_registration(db_path, telegram_id)

        senet_user = senet.get_user(login)
        senet_user_id: str = senet_user.user_id if senet_user else f"senet_{login.lower()}"

        # Создаём пользователя, если ещё нет
        existing = await get_user(db_path, telegram_id)
        if existing is None:
            await create_user(
                db_path=db_path,
                telegram_id=telegram_id,
                login=login,
                display_name=pending["display_name"] if pending else None,
                phone=pending["phone"] if pending else None,
            )

        await link_senet_user(
            db_path=db_path,
            telegram_id=telegram_id,
            senet_user_id=senet_user_id,
            senet_login=login,
        )
        await reset_senet_verify_attempts(db_path, telegram_id)
        await update_last_activity(db_path, telegram_id)

        # Очищаем pending
        await clear_pending_registration(db_path, telegram_id)

        from handlers.commands import _build_main_menu

        await update.message.reply_text(
            MESSAGES["verification_success"].format(login=escape(login)),
            reply_markup=_build_main_menu(),
        )
        logger.info(
            "Верификация успешна: telegram_id=%d, login=%r", telegram_id, login
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
    """
    Обрабатывает action="request_verification_code" от Web App.

    Генерирует код LOR-VRFY-XXXX, сохраняет в БД, уведомляет администраторов.

    Args:
        update: апдейт от Telegram.
        context: контекст PTB.
        data: распарсенный JSON от Web App.
    """
    if update.effective_user is None or update.message is None:
        return

    telegram_id: int = update.effective_user.id
    db_path: str = context.bot_data["db_path"]
    initial_admin_ids: list[int] = context.bot_data.get("initial_admin_ids", [])

    login: str = str(data.get("login", "")).strip()

    try:
        if not login:
            await update.message.reply_text(MESSAGES["error_invalid_data"])
            return

        can_proceed, lock_msg = await _check_verification_lock(db_path, telegram_id)
        if not can_proceed:
            await update.message.reply_text(lock_msg)
            return

        code: str = generate_verification_code()
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=CODE_TTL_SECONDS)

        await save_verification_code(
            db_path=db_path,
            telegram_id=telegram_id,
            senet_login=login,
            code=code,
            expires_at=expires_at,
        )

        # Уведомляем администраторов — не прерываем при ошибке уведомления
        admin_text = MESSAGES["admin_verify_request"].format(
            telegram_id=telegram_id,
            login=login,
            code=code,
        )
        for admin_id in initial_admin_ids:
            try:
                await context.bot.send_message(chat_id=admin_id, text=admin_text)
            except Exception:
                logger.warning(
                    "Не удалось уведомить admin_id=%d о коде %r", admin_id, code
                )

        await update.message.reply_text(
            MESSAGES["verification_code_sent"].format(
                code=code,
                minutes=CODE_TTL_SECONDS // 60,
            )
        )
        logger.info(
            "Код выдан: telegram_id=%d, login=%r, code=%r",
            telegram_id, login, code,
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
    """
    Обрабатывает action="booking" от Web App.

    Делегирует всю бизнес-логику в services/booking.py.
    Хендлер отвечает только за: получение telegram_id, вызов сервиса,
    форматирование и отправку ответа.

    Безопасность:
      - telegram_id берётся из update.effective_user.id, не из пейлоада.
      - Цена пересчитывается в сервисе; total_price из пейлоада игнорируется.
      - Все строки пользователя экранируются через html.escape перед выводом.

    Args:
        update: апдейт от Telegram.
        context: контекст PTB с bot_data.
        data: распарсенный JSON от Web App.
    """
    if update.effective_user is None or update.message is None:
        return

    telegram_id: int = update.effective_user.id
    db_path: str = context.bot_data["db_path"]

    try:
        from services.booking import create_booking

        result, error_msg = await create_booking(
            db_path=db_path,
            telegram_id=telegram_id,
            raw_data=data,
        )

        if error_msg is not None:
            await update.message.reply_text(error_msg)
            return

        # Экранируем данные брони перед подстановкой в сообщение
        await update.message.reply_text(
            MESSAGES["booking_success"].format(
                code=escape(result.booking_code),
                date=escape(result.date),
                time_from=escape(result.time_from),
                time_to=escape(result.time_to),
                pc_list=escape(result.pc_list),
                total_price=result.total_price,
            )
        )

    except Exception:
        logger.exception("Ошибка в _handle_booking для telegram_id=%d", telegram_id)
        await update.message.reply_text(MESSAGES["error_generic"])


# ---------------------------------------------------------------------------
# action: "cancel_booking"
# ---------------------------------------------------------------------------


async def _handle_cancel_booking(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    data: dict,
) -> None:
    """
    Обрабатывает action="cancel_booking" от Web App.

    Валидирует booking_id из пейлоада (должен быть положительным int),
    затем делегирует в services/booking.py.

    Args:
        update: апдейт от Telegram.
        context: контекст PTB с bot_data.
        data: распарсенный JSON от Web App.
    """
    if update.effective_user is None or update.message is None:
        return

    telegram_id: int = update.effective_user.id
    db_path: str = context.bot_data["db_path"]

    # Валидируем booking_id здесь, до передачи в сервис
    raw_booking_id = data.get("booking_id")
    if not isinstance(raw_booking_id, int) or raw_booking_id <= 0:
        await update.message.reply_text(MESSAGES["error_invalid_data"])
        return

    try:
        from services.booking import cancel_booking

        result, error_msg = await cancel_booking(
            db_path=db_path,
            telegram_id=telegram_id,
            booking_id=raw_booking_id,
        )

        if error_msg is not None:
            await update.message.reply_text(error_msg)
            return

        await update.message.reply_text(
            MESSAGES["booking_cancelled"].format(code=escape(result.booking_code))
        )

    except Exception:
        logger.exception(
            "Ошибка в _handle_cancel_booking для telegram_id=%d, booking_id=%s",
            telegram_id, raw_booking_id,
        )
        await update.message.reply_text(MESSAGES["error_generic"])

"""
services/booking.py — сервисный слой бронирования LORAN.CYBER.

Содержит всю бизнес-логику: валидацию, проверку лимитов, расчёт цены,
проверку конфликтов, создание и отмену брони.

Архитектура:
  Хендлер в webapp.py вызывает create_booking() или cancel_booking().
  Эти функции возвращают (result, error_message).
  При ошибке result=None, при успехе error_message=None.

Безопасность:
  - Цена пересчитывается здесь, значение от клиента игнорируется.
  - Права пользователя проверяются до любой записи в БД.
  - Все даты и времена валидируются строго — нет eval, нет datetime.strptime без try/except.
  - Booking code генерируется через secrets.
"""

import logging
import re
import secrets
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

from db import queries
from services.pricing import (
    VALID_PACKAGES,
    VALID_ZONES,
    PricingError,
    calculate_booking_price,
    validate_pc_zone,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Константы бизнес-правил
# ---------------------------------------------------------------------------

MAX_PCS_PER_BOOKING: int = 5       # максимум ПК в одной брони
MAX_TOTAL_PCS: int = 5             # суммарный лимит активных ПК
MAX_DAYS_AHEAD: int = 2            # максимум дней вперёд
CANCEL_BEFORE_MINUTES: int = 30    # отмена не позже чем за N минут до начала

# Диапазон допустимых PC ID (1–23 согласно ТЗ)
PC_ID_MIN: int = 1
PC_ID_MAX: int = 23

# Алфавит для кода брони (без похожих символов)
BOOKING_CODE_ALPHABET: str = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
BOOKING_CODE_SUFFIX_LEN: int = 3

# Формат времени для валидации
_TIME_RE: re.Pattern[str] = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_DATE_RE: re.Pattern[str] = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ---------------------------------------------------------------------------
# Типы результатов
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BookingResult:
    """Результат успешного создания брони."""

    booking_id: int
    booking_code: str
    date: str
    time_from: str
    time_to: str
    total_price: int
    pc_list: str          # "1, 2, 19" для отображения в сообщении
    zone_summary: str     # "MAIN×2, BOOTCAMP×1" для отображения


@dataclass(frozen=True)
class CancelResult:
    """Результат успешной отмены брони."""

    booking_code: str


# ---------------------------------------------------------------------------
# Валидация входных данных
# ---------------------------------------------------------------------------


class BookingValidationError(ValueError):
    """Ошибка валидации данных бронирования с пользовательским сообщением."""

    def __init__(self, user_message: str) -> None:
        super().__init__(user_message)
        self.user_message = user_message


def _validate_date_time(
    date_str: str,
    time_from_str: str,
    time_to_str: str,
) -> tuple[date, time, time]:
    """
    Валидирует и парсит дату и временной интервал брони.

    Args:
        date_str: дата в формате YYYY-MM-DD.
        time_from_str: начало в формате HH:MM.
        time_to_str: конец в формате HH:MM.

    Returns:
        Кортеж (date, time_from, time_to).

    Raises:
        BookingValidationError: если формат неверен, дата в прошлом,
            интервал некорректен или превышает лимит 2 суток.
    """
    from locales.ru import MESSAGES

    # Формат даты
    if not _DATE_RE.match(date_str):
        raise BookingValidationError(MESSAGES["error_booking_invalid_date"])

    # Формат времени
    if not _TIME_RE.match(time_from_str) or not _TIME_RE.match(time_to_str):
        raise BookingValidationError(MESSAGES["error_booking_invalid_date"])

    try:
        booking_date = date.fromisoformat(date_str)
        tf_h, tf_m = map(int, time_from_str.split(":"))
        tt_h, tt_m = map(int, time_to_str.split(":"))
        t_from = time(tf_h, tf_m)
        t_to   = time(tt_h, tt_m)
    except ValueError:
        raise BookingValidationError(MESSAGES["error_booking_invalid_date"])

    # Интервал должен быть положительным
    if t_from >= t_to:
        raise BookingValidationError(MESSAGES["error_booking_invalid_date"])

    # Не в прошлом (сравниваем по дате + времени UTC)
    now_utc = datetime.now(timezone.utc)
    booking_start = datetime.combine(booking_date, t_from, tzinfo=timezone.utc)
    if booking_start < now_utc:
        raise BookingValidationError(MESSAGES["error_booking_past_date"])

    # Не позднее 2 суток вперёд
    max_date = now_utc.date() + timedelta(days=MAX_DAYS_AHEAD)
    if booking_date > max_date:
        raise BookingValidationError(MESSAGES["error_booking_date_range"])

    return booking_date, t_from, t_to


def _validate_pcs(pcs_raw: object) -> list[dict]:
    """
    Валидирует и очищает список ПК из пейлоада.

    Проверяет:
      - pcs_raw — это непустой список.
      - Количество ПК <= MAX_PCS_PER_BOOKING.
      - Каждый элемент содержит pc_id (int, 1–23) и zone (допустимая строка).
      - PC ID уникальны.
      - Каждый PC ID соответствует заявленной зоне.

    Args:
        pcs_raw: сырое значение поля "pcs" из пейлоада.

    Returns:
        Очищенный список [{"pc_id": int, "zone": str}, ...].

    Raises:
        BookingValidationError: при любом нарушении.
    """
    from locales.ru import MESSAGES

    if not isinstance(pcs_raw, list) or len(pcs_raw) == 0:
        raise BookingValidationError(MESSAGES["error_invalid_data"])

    if len(pcs_raw) > MAX_PCS_PER_BOOKING:
        raise BookingValidationError(MESSAGES["error_booking_pc_limit"])

    seen_ids: set[int] = set()
    validated: list[dict] = []

    for item in pcs_raw:
        if not isinstance(item, dict):
            raise BookingValidationError(MESSAGES["error_invalid_data"])

        # pc_id — целое число в диапазоне 1–23
        raw_pc_id = item.get("pc_id")
        if not isinstance(raw_pc_id, int) or not (PC_ID_MIN <= raw_pc_id <= PC_ID_MAX):
            raise BookingValidationError(MESSAGES["error_invalid_data"])

        # zone — допустимая строка
        raw_zone = str(item.get("zone", "")).strip().lower()
        if raw_zone not in VALID_ZONES:
            raise BookingValidationError(MESSAGES["error_invalid_data"])

        # Уникальность PC ID
        if raw_pc_id in seen_ids:
            raise BookingValidationError(MESSAGES["error_invalid_data"])
        seen_ids.add(raw_pc_id)

        # PC ID соответствует зоне
        if not validate_pc_zone(raw_pc_id, raw_zone):
            raise BookingValidationError(MESSAGES["error_invalid_data"])

        validated.append({"pc_id": raw_pc_id, "zone": raw_zone})

    return validated


def _validate_package(package_id: object) -> str:
    """
    Валидирует идентификатор пакета.

    Args:
        package_id: сырое значение из пейлоада.

    Returns:
        Нормализованный package_id в нижнем регистре.

    Raises:
        BookingValidationError: если пакет неизвестен.
    """
    from locales.ru import MESSAGES

    if not isinstance(package_id, str):
        raise BookingValidationError(MESSAGES["error_invalid_data"])

    pkg = package_id.strip().lower()
    if pkg not in VALID_PACKAGES:
        raise BookingValidationError(MESSAGES["error_invalid_data"])

    return pkg


# ---------------------------------------------------------------------------
# Генерация кода брони
# ---------------------------------------------------------------------------


def _generate_booking_code(booking_id: int) -> str:
    """
    Генерирует уникальный код брони.

    Формат: LOR-{ID}-{3 символа}.
    Пример: LOR-42-K7N.

    Args:
        booking_id: ID только что созданной записи в bookings.

    Returns:
        str: код брони.
    """
    suffix = "".join(
        secrets.choice(BOOKING_CODE_ALPHABET) for _ in range(BOOKING_CODE_SUFFIX_LEN)
    )
    return f"LOR-{booking_id}-{suffix}"


# ---------------------------------------------------------------------------
# Публичный API сервиса
# ---------------------------------------------------------------------------


async def create_booking(
    db_path: str,
    telegram_id: int,
    raw_data: dict,
) -> tuple[Optional[BookingResult], Optional[str]]:
    """
    Создаёт бронирование, выполняя полный цикл проверок.

    Порядок проверок:
      1. Пользователь зарегистрирован и активен.
      2. Senet привязан.
      3. Валидация дата/время.
      4. Валидация списка ПК.
      5. Валидация пакета.
      6. Лимит ПК в этой брони (<= 5).
      7. Суммарный лимит активных ПК (<= 5).
      8. Расчёт цены на сервере.
      9. Проверка конфликтов по времени.
      10. Запись в БД.
      11. Обновление кода брони в записи.

    Args:
        db_path: путь к файлу БД.
        telegram_id: ID пользователя из Telegram (из update, не из пейлоада).
        raw_data: распарсенный JSON от Web App.

    Returns:
        (BookingResult, None) при успехе.
        (None, "сообщение об ошибке") при любой ошибке.
    """
    from locales.ru import MESSAGES

    try:
        # 1. Пользователь активен?
        user = await queries.get_user(db_path, telegram_id)
        if user is None or user["status"] != "active":
            return None, MESSAGES["error_not_registered"]

        # 2. Senet привязан?
        if not user["senet_verified"]:
            return None, MESSAGES["error_booking_senet_required"]

        # 3. Дата/время
        date_str  = str(raw_data.get("date", "")).strip()
        time_from = str(raw_data.get("time_from", "")).strip()
        time_to   = str(raw_data.get("time_to", "")).strip()

        try:
            booking_date, t_from, t_to = _validate_date_time(date_str, time_from, time_to)
        except BookingValidationError as exc:
            return None, exc.user_message

        # 4. Список ПК
        try:
            pcs = _validate_pcs(raw_data.get("pcs"))
        except BookingValidationError as exc:
            return None, exc.user_message

        # 5. Пакет
        try:
            package_id = _validate_package(raw_data.get("package_id"))
        except BookingValidationError as exc:
            return None, exc.user_message

        # 6. Лимит ПК в этой брони
        if len(pcs) > MAX_PCS_PER_BOOKING:
            return None, MESSAGES["error_booking_pc_limit"]

        # 7. Суммарный лимит активных ПК
        current_active_pcs = await queries.count_active_pcs(db_path, telegram_id)
        if current_active_pcs + len(pcs) > MAX_TOTAL_PCS:
            return None, MESSAGES["error_booking_total_pc_limit"].format(
                current=current_active_pcs
            )

        # 8. Расчёт цены на сервере (значение total_price из пейлоада игнорируется)
        try:
            price_breakdown = calculate_booking_price(pcs, package_id)
        except PricingError as exc:
            logger.warning("Ошибка расчёта цены для telegram_id=%d: %s", telegram_id, exc)
            return None, MESSAGES["error_invalid_data"]

        # 9. Конфликты по времени
        pc_ids = [pc["pc_id"] for pc in pcs]
        conflicts = await queries.get_conflicting_bookings(
            db_path, pc_ids, date_str, time_from, time_to
        )
        if conflicts:
            conflict_pcs = ", ".join(str(c["id"]) for c in conflicts)
            return None, MESSAGES["error_booking_pc_conflict"].format(
                conflicts=conflict_pcs
            )

        # 10. Запись в БД (временный код — обновим после получения ID)
        temp_code = f"LOR-TEMP-{secrets.token_hex(4).upper()}"
        booking_id = await queries.create_booking(
            db_path=db_path,
            telegram_id=telegram_id,
            booking_code=temp_code,
            date=date_str,
            time_from=time_from,
            time_to=time_to,
            total_price=price_breakdown.total_price,
        )

        # 11. Финальный код брони с реальным ID
        booking_code = _generate_booking_code(booking_id)
        await queries.update_booking_code(db_path, booking_id, booking_code)

        # Записываем ПК
        for pc in pcs:
            zone = pc["zone"]
            price_per_pc = price_breakdown.zone_prices[zone]
            await queries.add_booking_pc(
                db_path=db_path,
                booking_id=booking_id,
                pc_id=pc["pc_id"],
                zone=zone.upper(),
                price_per_pc=price_per_pc,
            )

        # Формируем строки для сообщения
        pc_list = ", ".join(str(pc["pc_id"]) for pc in sorted(pcs, key=lambda x: x["pc_id"]))
        zone_parts = [
            f"{z.upper()}×{count}"
            for z, count in price_breakdown.zone_counts.items()
        ]
        zone_summary = ", ".join(zone_parts)

        logger.info(
            "Бронь создана: id=%d, code=%s, telegram_id=%d, pcs=%s, price=%d",
            booking_id, booking_code, telegram_id, pc_list, price_breakdown.total_price,
        )

        return BookingResult(
            booking_id=booking_id,
            booking_code=booking_code,
            date=date_str,
            time_from=time_from,
            time_to=time_to,
            total_price=price_breakdown.total_price,
            pc_list=pc_list,
            zone_summary=zone_summary,
        ), None

    except Exception:
        logger.exception("Неожиданная ошибка в create_booking для telegram_id=%d", telegram_id)
        from locales.ru import MESSAGES
        return None, MESSAGES["error_generic"]


async def cancel_booking(
    db_path: str,
    telegram_id: int,
    booking_id: int,
) -> tuple[Optional[CancelResult], Optional[str]]:
    """
    Отменяет бронирование пользователя.

    Проверки:
      1. Бронь существует.
      2. Бронь принадлежит данному пользователю.
      3. Статус — confirmed.
      4. До начала брони более CANCEL_BEFORE_MINUTES минут.

    Args:
        db_path: путь к файлу БД.
        telegram_id: ID пользователя из Telegram.
        booking_id: ID бронирования из пейлоада (int, уже валидирован хендлером).

    Returns:
        (CancelResult, None) при успехе.
        (None, "сообщение") при ошибке.
    """
    from locales.ru import MESSAGES

    try:
        booking = await queries.get_booking_by_id(db_path, booking_id)

        if booking is None:
            return None, MESSAGES["error_booking_not_found"]

        if booking["telegram_id"] != telegram_id:
            # Не раскрываем факт существования чужой брони
            return None, MESSAGES["error_booking_not_found"]

        if booking["status"] == "cancelled":
            return None, MESSAGES["error_booking_already_cancelled"]

        if booking["status"] != "confirmed":
            return None, MESSAGES["error_booking_not_found"]

        # Проверяем временной лимит отмены
        try:
            booking_start = datetime.combine(
                date.fromisoformat(booking["date"]),
                time.fromisoformat(booking["time_from"]),
                tzinfo=timezone.utc,
            )
        except (ValueError, TypeError):
            logger.error("Некорректные дата/время в брони id=%d", booking_id)
            return None, MESSAGES["error_generic"]

        now_utc = datetime.now(timezone.utc)
        minutes_until_start = (booking_start - now_utc).total_seconds() / 60

        if minutes_until_start < CANCEL_BEFORE_MINUTES:
            return None, MESSAGES["error_booking_cancel_late"]

        # Отменяем
        await queries.cancel_booking(
            db_path=db_path,
            booking_id=booking_id,
            cancelled_by="user",
            cancel_reason=None,
        )

        logger.info(
            "Бронь отменена: id=%d, code=%s, telegram_id=%d",
            booking_id, booking["booking_code"], telegram_id,
        )

        return CancelResult(booking_code=booking["booking_code"]), None

    except Exception:
        logger.exception(
            "Неожиданная ошибка в cancel_booking для telegram_id=%d, booking_id=%d",
            telegram_id, booking_id,
        )
        from locales.ru import MESSAGES
        return None, MESSAGES["error_generic"]

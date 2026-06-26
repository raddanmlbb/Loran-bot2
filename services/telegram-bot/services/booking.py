"""
services/booking.py — сервисный слой бронирования LORAN.CYBER.

Этап 5: расчёт цен через кэш из БД вместо хардкода.
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

MAX_PCS_PER_BOOKING: int = 5
MAX_TOTAL_PCS: int = 5
MAX_DAYS_AHEAD: int = 2
CANCEL_BEFORE_MINUTES: int = 30

PC_ID_MIN: int = 1
PC_ID_MAX: int = 23

BOOKING_CODE_ALPHABET: str = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
BOOKING_CODE_SUFFIX_LEN: int = 3

_TIME_RE: re.Pattern[str] = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_DATE_RE: re.Pattern[str] = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class BookingResult:
    booking_id: int
    booking_code: str
    date: str
    time_from: str
    time_to: str
    total_price: int
    pc_list: str
    zone_summary: str


@dataclass(frozen=True)
class CancelResult:
    booking_code: str


class BookingValidationError(ValueError):
    def __init__(self, user_message: str) -> None:
        super().__init__(user_message)
        self.user_message = user_message


def _validate_date_time(
    date_str: str,
    time_from_str: str,
    time_to_str: str,
) -> tuple[date, time, time]:
    from locales.ru import MESSAGES

    if not _DATE_RE.match(date_str):
        raise BookingValidationError(MESSAGES["error_booking_invalid_date"])
    if not _TIME_RE.match(time_from_str) or not _TIME_RE.match(time_to_str):
        raise BookingValidationError(MESSAGES["error_booking_invalid_date"])

    try:
        booking_date = date.fromisoformat(date_str)
        tf_h, tf_m = map(int, time_from_str.split(":"))
        tt_h, tt_m = map(int, time_to_str.split(":"))
        t_from = time(tf_h, tf_m)
        t_to = time(tt_h, tt_m)
    except ValueError:
        raise BookingValidationError(MESSAGES["error_booking_invalid_date"])

    if t_from >= t_to:
        raise BookingValidationError(MESSAGES["error_booking_invalid_date"])

    now_utc = datetime.now(timezone.utc)
    booking_start = datetime.combine(booking_date, t_from, tzinfo=timezone.utc)
    if booking_start < now_utc:
        raise BookingValidationError(MESSAGES["error_booking_past_date"])

    max_date = now_utc.date() + timedelta(days=MAX_DAYS_AHEAD)
    if booking_date > max_date:
        raise BookingValidationError(MESSAGES["error_booking_date_range"])

    return booking_date, t_from, t_to


def _validate_pcs(pcs_raw: object) -> list[dict]:
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

        raw_pc_id = item.get("pc_id")
        if not isinstance(raw_pc_id, int) or not (PC_ID_MIN <= raw_pc_id <= PC_ID_MAX):
            raise BookingValidationError(MESSAGES["error_invalid_data"])

        raw_zone = str(item.get("zone", "")).strip().lower()
        if raw_zone not in VALID_ZONES:
            raise BookingValidationError(MESSAGES["error_invalid_data"])

        if raw_pc_id in seen_ids:
            raise BookingValidationError(MESSAGES["error_invalid_data"])
        seen_ids.add(raw_pc_id)

        if not validate_pc_zone(raw_pc_id, raw_zone):
            raise BookingValidationError(MESSAGES["error_invalid_data"])

        validated.append({"pc_id": raw_pc_id, "zone": raw_zone})

    return validated


def _validate_package(package_id: object) -> str:
    from locales.ru import MESSAGES

    if not isinstance(package_id, str):
        raise BookingValidationError(MESSAGES["error_invalid_data"])

    pkg = package_id.strip().lower()
    if pkg not in VALID_PACKAGES:
        raise BookingValidationError(MESSAGES["error_invalid_data"])
    return pkg


def _generate_booking_code(booking_id: int) -> str:
    suffix = "".join(
        secrets.choice(BOOKING_CODE_ALPHABET) for _ in range(BOOKING_CODE_SUFFIX_LEN)
    )
    return f"LOR-{booking_id}-{suffix}"


async def create_booking(
    db_path: str,
    telegram_id: int,
    raw_data: dict,
) -> tuple[Optional[BookingResult], Optional[str]]:
    from locales.ru import MESSAGES

    try:
        user = await queries.get_user(db_path, telegram_id)
        if user is None or user["status"] != "active":
            return None, MESSAGES["error_not_registered"]

        if not user["senet_verified"]:
            return None, MESSAGES["error_booking_senet_required"]

        date_str = str(raw_data.get("date", "")).strip()
        time_from = str(raw_data.get("time_from", "")).strip()
        time_to = str(raw_data.get("time_to", "")).strip()

        try:
            booking_date, t_from, t_to = _validate_date_time(date_str, time_from, time_to)
        except BookingValidationError as exc:
            return None, exc.user_message

        try:
            pcs = _validate_pcs(raw_data.get("pcs"))
        except BookingValidationError as exc:
            return None, exc.user_message

        try:
            package_id = _validate_package(raw_data.get("package_id"))
        except BookingValidationError as exc:
            return None, exc.user_message

        if len(pcs) > MAX_PCS_PER_BOOKING:
            return None, MESSAGES["error_booking_pc_limit"]

        current_active_pcs = await queries.count_active_pcs(db_path, telegram_id)
        if current_active_pcs + len(pcs) > MAX_TOTAL_PCS:
            return None, MESSAGES["error_booking_total_pc_limit"].format(
                current=current_active_pcs
            )

        # Расчёт цены из кэша БД
        try:
            breakdown = await calculate_booking_price(db_path, pcs, package_id)
        except PricingError as exc:
            return None, str(exc)

        # Проверка конфликтов
        pc_ids = [pc["pc_id"] for pc in pcs]
        conflicts = await queries.check_pc_conflicts(
            db_path, date_str, time_from, time_to, pc_ids
        )
        if conflicts:
            conflict_str = ", ".join(f"ПК {c}" for c in conflicts)
            return None, MESSAGES["error_booking_pc_conflict"].format(conflicts=conflict_str)

        # Запись в БД
        booking_id = await queries.create_booking(
            db_path=db_path,
            telegram_id=telegram_id,
            date=date_str,
            time_from=time_from,
            time_to=time_to,
            total_price=breakdown.total_price,
        )

        booking_code = _generate_booking_code(booking_id)
        await queries.update_booking_code(db_path, booking_id, booking_code)

        for pc in pcs:
            await queries.add_booking_pc(
                db_path=db_path,
                booking_id=booking_id,
                pc_id=pc["pc_id"],
                zone=pc["zone"],
                price_per_pc=breakdown.zone_prices[pc["zone"]],
            )

        pc_list_str = ", ".join(str(pc["pc_id"]) for pc in pcs)
        zone_summary_parts = []
        for zone, count in breakdown.zone_counts.items():
            zone_summary_parts.append(f"{zone.upper()}×{count}")
        zone_summary = ", ".join(zone_summary_parts)

        return BookingResult(
            booking_id=booking_id,
            booking_code=booking_code,
            date=date_str,
            time_from=time_from,
            time_to=time_to,
            total_price=breakdown.total_price,
            pc_list=pc_list_str,
            zone_summary=zone_summary,
        ), None

    except Exception as exc:
        logger.exception("Ошибка при создании брони для telegram_id=%d: %s", telegram_id, exc)
        from locales.ru import MESSAGES
        return None, MESSAGES["error_generic"]


async def cancel_booking(
    db_path: str,
    telegram_id: int,
    booking_code: str,
) -> tuple[Optional[CancelResult], Optional[str]]:
    from locales.ru import MESSAGES

    try:
        booking = await queries.get_booking_by_code(db_path, booking_code)
        if booking is None:
            return None, MESSAGES["error_booking_not_found"]

        if booking["telegram_id"] != telegram_id:
            return None, MESSAGES["error_booking_not_yours"]

        if booking["status"] == "cancelled":
            return None, MESSAGES["error_booking_already_cancelled"]

        now_utc = datetime.now(timezone.utc)
        booking_start = datetime.combine(
            date.fromisoformat(booking["date"]),
            time.fromisoformat(booking["time_from"]),
            tzinfo=timezone.utc,
        )
        if booking_start - now_utc < timedelta(minutes=CANCEL_BEFORE_MINUTES):
            return None, MESSAGES["error_booking_cancel_late"]

        await queries.cancel_booking(
            db_path=db_path,
            booking_id=booking["id"],
            cancelled_by="user",
            reason="",
        )

        return CancelResult(booking_code=booking_code), None

    except Exception as exc:
        logger.exception("Ошибка при отмене брони %s: %s", booking_code, exc)
        from locales.ru import MESSAGES
        return None, MESSAGES["error_generic"]

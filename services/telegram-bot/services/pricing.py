"""
services/pricing.py — расчёт цен LORAN.CYBER.

Этап 5: цены загружаются из Senet API и кэшируются в таблице pricing.
При недоступности Senet используется последняя сохранённая копия.
Цены обновляются раз в час через PTB job_queue.
"""

import logging
from dataclasses import dataclass
from typing import Optional

from db.queries import get_all_pricing, get_price, upsert_pricing
from senet_api import SenetAPI, SenetAPIError

logger = logging.getLogger(__name__)

# Допустимые зоны и ID пакетов
VALID_ZONES: frozenset[str] = frozenset({"main", "bootcamp"})
VALID_PACKAGES: frozenset[str] = frozenset({"1h", "3h", "night", "day", "24h"})

# PC ID по зонам
ZONE_PC_RANGES: dict[str, range] = {
    "main":     range(1, 19),   # 1–18
    "bootcamp": range(19, 24),  # 19–23
}


@dataclass(frozen=True)
class PriceBreakdown:
    """Детализация расчёта цены брони."""
    zone_prices: dict[str, int]
    zone_counts: dict[str, int]
    total_price: int


class PricingError(ValueError):
    """Ошибка расчёта цены."""


async def sync_prices_from_senet(db_path: str, senet: SenetAPI) -> bool:
    """
    Загружает цены из Senet и сохраняет в таблицу pricing.

    Returns:
        True если синхронизация прошла успешно, False если Senet недоступен.
    """
    try:
        packages = senet.get_pricing()
        for pkg in packages:
            await upsert_pricing(
                db_path=db_path,
                zone=pkg.zone,
                package_id=pkg.package_id,
                price=pkg.price,
                hours=pkg.hours,
                fixed_start=pkg.fixed_start,
                fixed_end=pkg.fixed_end,
                is_popular=pkg.is_popular,
            )
        logger.info("Цены обновлены из Senet: %d пакетов", len(packages))
        return True
    except SenetAPIError as exc:
        logger.warning("Не удалось обновить цены из Senet: %s. Используется кэш.", exc)
        return False


async def get_price_per_pc(db_path: str, zone: str, package_id: str) -> int:
    """
    Возвращает цену за один ПК для заданной зоны и пакета.
    Берёт из кэша в БД.

    Raises:
        PricingError: если зона/пакет не существуют или цена не найдена.
    """
    zone_lower = zone.lower()
    pkg_lower = package_id.lower()

    if zone_lower not in VALID_ZONES:
        raise PricingError(f"Неизвестная зона: {zone!r}")
    if pkg_lower not in VALID_PACKAGES:
        raise PricingError(f"Неизвестный пакет: {package_id!r}")

    price = await get_price(db_path, zone_lower, pkg_lower)
    if price is None:
        raise PricingError(f"Цена не найдена для {zone!r}/{package_id!r}. Обновите прайс-лист.")
    return price


async def calculate_booking_price(
    db_path: str,
    pcs: list[dict],
    package_id: str,
) -> PriceBreakdown:
    """
    Рассчитывает итоговую стоимость бронирования.
    Значение total_price из Web App полностью игнорируется.
    """
    zone_counts: dict[str, int] = {}
    zone_prices: dict[str, int] = {}

    for pc in pcs:
        zone = str(pc.get("zone", "")).lower()
        if zone not in VALID_ZONES:
            raise PricingError(f"Неизвестная зона: {zone!r}")

        price = await get_price_per_pc(db_path, zone, package_id)
        zone_counts[zone] = zone_counts.get(zone, 0) + 1
        zone_prices[zone] = price

    total = sum(zone_prices[z] * zone_counts[z] for z in zone_counts)

    return PriceBreakdown(
        zone_prices=zone_prices,
        zone_counts=zone_counts,
        total_price=total,
    )


def validate_pc_zone(pc_id: int, zone: str) -> bool:
    """Проверяет, что PC ID соответствует заявленной зоне."""
    zone_lower = zone.lower()
    pc_range = ZONE_PC_RANGES.get(zone_lower)
    if pc_range is None:
        return False
    return pc_id in pc_range

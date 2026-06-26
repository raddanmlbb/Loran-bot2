"""
services/pricing.py — серверный расчёт цен LORAN.CYBER.

Цены рассчитываются исключительно на сервере.
Значение total_price из Web App-пейлоада ВСЕГДА игнорируется.

Структура пакетов:
  Каждый пакет задаётся как (hours, price_per_pc), где hours=None
  означает фиксированный временной слот (ночь, день, сутки).

Добавление новых цен:
  - Изменить MOCK_PRICES или реализовать загрузку из таблицы pricing.
  - Не менять сигнатуры публичных функций.
"""

from dataclasses import dataclass
from typing import Optional

# ---------------------------------------------------------------------------
# Справочник цен (заглушка до реализации загрузки из таблицы pricing)
# ---------------------------------------------------------------------------
# Структура: zone -> package_id -> price_per_pc (тенге)
# package_id совпадает со значением поля "package_id" из пейлоада Web App.

MOCK_PRICES: dict[str, dict[str, int]] = {
    "main": {
        "1h":    1_500,
        "3h":    4_000,
        "night": 7_000,   # фиксированный ночной слот
        "day":   8_000,   # фиксированный дневной слот
        "24h":  15_000,
    },
    "bootcamp": {
        "1h":    3_000,
        "3h":    8_000,
        "night": 15_000,
        "day":   17_000,
        "24h":   30_000,
    },
}

# Допустимые зоны
VALID_ZONES: frozenset[str] = frozenset({"main", "bootcamp"})

# Допустимые ID пакетов
VALID_PACKAGES: frozenset[str] = frozenset(MOCK_PRICES["main"].keys())

# PC ID по зонам согласно ТЗ
ZONE_PC_RANGES: dict[str, range] = {
    "main":     range(1, 19),   # 1–18
    "bootcamp": range(19, 24),  # 19–23
}


@dataclass(frozen=True)
class PriceBreakdown:
    """Детализация расчёта цены брони."""

    zone_prices: dict[str, int]   # zone -> price_per_pc
    zone_counts: dict[str, int]   # zone -> количество ПК
    total_price: int              # итоговая сумма в тенге


class PricingError(ValueError):
    """Ошибка расчёта цены (неверная зона или пакет)."""


def get_price_per_pc(zone: str, package_id: str) -> int:
    """
    Возвращает цену за один ПК для заданной зоны и пакета.

    Args:
        zone: зона — "main" или "bootcamp" (нижний регистр).
        package_id: идентификатор пакета — "1h", "3h", "night", "day", "24h".

    Returns:
        int: цена в тенге за один ПК.

    Raises:
        PricingError: если зона или пакет не существуют.
    """
    zone_lower = zone.lower()
    if zone_lower not in MOCK_PRICES:
        raise PricingError(f"Неизвестная зона: {zone!r}")

    pkg = package_id.lower()
    if pkg not in MOCK_PRICES[zone_lower]:
        raise PricingError(f"Неизвестный пакет: {package_id!r} для зоны {zone!r}")

    return MOCK_PRICES[zone_lower][pkg]


def calculate_booking_price(
    pcs: list[dict],
    package_id: str,
) -> PriceBreakdown:
    """
    Рассчитывает итоговую стоимость бронирования на сервере.

    Принимает список ПК из валидированного пейлоада.
    Значение total_price из пейлоада Web App полностью игнорируется.

    Args:
        pcs: список словарей {"pc_id": int, "zone": str}.
             Зона должна быть "main" или "bootcamp" (регистр нечувствителен).
        package_id: идентификатор пакета ("1h", "3h", "night", "day", "24h").

    Returns:
        PriceBreakdown с детализацией и итоговой суммой.

    Raises:
        PricingError: если зона или пакет неверные.

    Example:
        pcs = [{"pc_id": 1, "zone": "main"}, {"pc_id": 19, "zone": "bootcamp"}]
        bd = calculate_booking_price(pcs, "3h")
        # bd.total_price == 12_000  (4000 + 8000)
    """
    zone_counts: dict[str, int] = {}
    zone_prices: dict[str, int] = {}

    for pc in pcs:
        zone = str(pc.get("zone", "")).lower()
        if zone not in VALID_ZONES:
            raise PricingError(f"Неизвестная зона: {zone!r}")

        price = get_price_per_pc(zone, package_id)
        zone_counts[zone] = zone_counts.get(zone, 0) + 1
        zone_prices[zone] = price  # цена одинакова для всех ПК одной зоны

    total = sum(zone_prices[z] * zone_counts[z] for z in zone_counts)

    return PriceBreakdown(
        zone_prices=zone_prices,
        zone_counts=zone_counts,
        total_price=total,
    )


def validate_pc_zone(pc_id: int, zone: str) -> bool:
    """
    Проверяет, что PC ID соответствует заявленной зоне.

    Args:
        pc_id: номер ПК (1–23).
        zone: зона — "main" или "bootcamp".

    Returns:
        True, если PC ID принадлежит зоне.
    """
    zone_lower = zone.lower()
    pc_range = ZONE_PC_RANGES.get(zone_lower)
    if pc_range is None:
        return False
    return pc_id in pc_range

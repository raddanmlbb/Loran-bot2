"""
senet_api.py — адаптер интеграции с CRM Senet.

Режимы работы:
  "mock" — возвращает тестовые данные для разработки.
  "live" — выполняет HTTP-запросы к реальному Senet API.

Переключение через SENET_MODE в переменных окружения.
"""

import logging
from dataclasses import dataclass
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SenetUser:
    """Представление пользователя Senet."""

    user_id: str
    login: str
    phone_last4: Optional[str] = None


@dataclass
class SenetPricingPackage:
    """Пакет цен из Senet."""

    zone: str
    package_id: str
    price: int
    hours: Optional[int] = None
    fixed_start: Optional[str] = None
    fixed_end: Optional[str] = None
    is_popular: bool = False


class SenetAPIError(Exception):
    """Базовое исключение для ошибок Senet API."""


class SenetAPI:
    """
    Адаптер для работы с Senet CRM.

    Args:
        mode: "mock" для разработки, "live" для продакшна.
        api_url: базовый URL Senet API (для live-режима).
        api_key: ключ приложения Senet (для live-режима).
    """

    # Тестовые данные для mock-режима.
    _MOCK_USERS: dict[str, str] = {
        "cyber_stalker": "4567",
        "neiro_girl":    "8901",
        "gamer_kz":      "1234",
        "admin":         "0000",
    }

    # Мок-прайсы для разработки
    _MOCK_PRICES: list[dict] = [
        {"zone": "main",     "package_id": "1h",    "price": 1500, "hours": 1},
        {"zone": "main",     "package_id": "3h",    "price": 4000, "hours": 3},
        {"zone": "main",     "package_id": "night",  "price": 7000, "hours": None,
         "fixed_start": "23:00", "fixed_end": "08:00"},
        {"zone": "main",     "package_id": "day",   "price": 8000, "hours": None,
         "fixed_start": "09:00", "fixed_end": "21:00"},
        {"zone": "main",     "package_id": "24h",   "price": 15000, "hours": 24},
        {"zone": "bootcamp", "package_id": "1h",    "price": 3000, "hours": 1},
        {"zone": "bootcamp", "package_id": "3h",    "price": 8000, "hours": 3},
        {"zone": "bootcamp", "package_id": "night",  "price": 15000, "hours": None,
         "fixed_start": "23:00", "fixed_end": "08:00"},
        {"zone": "bootcamp", "package_id": "day",   "price": 17000, "hours": None,
         "fixed_start": "09:00", "fixed_end": "21:00"},
        {"zone": "bootcamp", "package_id": "24h",   "price": 30000, "hours": 24},
    ]

    def __init__(
        self,
        mode: str = "mock",
        api_url: str = "",
        api_key: str = "",
    ) -> None:
        if mode not in ("mock", "live"):
            raise ValueError(f"Недопустимый режим Senet API: {mode!r}.")
        self.mode = mode
        self._api_url = api_url.rstrip("/")
        self._api_key = api_key
        logger.info("SenetAPI инициализирован в режиме: %s", mode)

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    # ------------------------------------------------------------------
    # Пользователи
    # ------------------------------------------------------------------

    def user_exists(self, login: str) -> bool:
        """Проверяет, существует ли пользователь в Senet."""
        if self.mode == "mock":
            result = login.lower() in self._MOCK_USERS
            logger.debug("SenetAPI.user_exists(mock): login=%r → %s", login, result)
            return result

        try:
            resp = httpx.get(
                f"{self._api_url}/users",
                params={"login": login},
                headers=self._headers(),
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            return bool(data.get("exists", False))
        except httpx.HTTPError as exc:
            raise SenetAPIError(f"Ошибка Senet API: {exc}") from exc

    def create_user(
        self,
        login: str,
        password: str,
        phone: Optional[str] = None,
    ) -> SenetUser:
        """
        Создаёт нового пользователя в Senet.

        POST /api/users с телом { login, password, phone }.
        Пароль передаётся в Senet и НЕ хранится в локальной БД.
        """
        if self.mode == "mock":
            user = SenetUser(
                user_id=f"senet_{login.lower()}",
                login=login,
                phone_last4=phone[-4:] if phone and len(phone) >= 4 else None,
            )
            logger.debug("SenetAPI.create_user(mock): создан пользователь %r", login)
            return user

        try:
            payload: dict = {"login": login, "password": password}
            if phone:
                payload["phone"] = phone
            resp = httpx.post(
                f"{self._api_url}/users",
                json=payload,
                headers=self._headers(),
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            return SenetUser(
                user_id=str(data.get("id", "")),
                login=data.get("login", login),
                phone_last4=phone[-4:] if phone and len(phone) >= 4 else None,
            )
        except httpx.HTTPError as exc:
            raise SenetAPIError(f"Ошибка создания пользователя Senet: {exc}") from exc

    def verify_phone_last4(self, login: str, phone_last4: str) -> bool:
        """Проверяет последние 4 цифры телефона пользователя Senet."""
        if not (len(phone_last4) == 4 and phone_last4.isdigit()):
            raise ValueError(f"phone_last4 должен быть строкой из 4 цифр: {phone_last4!r}")

        if self.mode == "mock":
            expected = self._MOCK_USERS.get(login.lower())
            result = expected == phone_last4
            logger.debug("SenetAPI.verify_phone_last4(mock): %r → %s", login, result)
            return result

        try:
            resp = httpx.post(
                f"{self._api_url}/users/verify_phone",
                json={"login": login, "phone_last4": phone_last4},
                headers=self._headers(),
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            return bool(data.get("verified", False))
        except httpx.HTTPError as exc:
            raise SenetAPIError(f"Ошибка верификации телефона Senet: {exc}") from exc

    def get_user(self, login: str) -> Optional[SenetUser]:
        """Возвращает данные пользователя Senet по логину."""
        if self.mode == "mock":
            if login.lower() not in self._MOCK_USERS:
                return None
            return SenetUser(
                user_id=f"senet_{login.lower()}",
                login=login,
                phone_last4=self._MOCK_USERS[login.lower()],
            )

        try:
            resp = httpx.get(
                f"{self._api_url}/users",
                params={"login": login},
                headers=self._headers(),
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            if not data.get("exists"):
                return None
            return SenetUser(
                user_id=str(data.get("id", "")),
                login=data.get("login", login),
                phone_last4=data.get("phone_last4"),
            )
        except httpx.HTTPError as exc:
            raise SenetAPIError(f"Ошибка получения пользователя Senet: {exc}") from exc

    # ------------------------------------------------------------------
    # Цены
    # ------------------------------------------------------------------

    def get_pricing(self) -> list[SenetPricingPackage]:
        """
        Загружает актуальные цены из Senet.

        GET /api/pricing → список пакетов по зонам.
        В mock-режиме возвращает встроенные тестовые данные.
        """
        if self.mode == "mock":
            result = []
            for item in self._MOCK_PRICES:
                result.append(SenetPricingPackage(
                    zone=item["zone"],
                    package_id=item["package_id"],
                    price=item["price"],
                    hours=item.get("hours"),
                    fixed_start=item.get("fixed_start"),
                    fixed_end=item.get("fixed_end"),
                    is_popular=item.get("is_popular", False),
                ))
            logger.debug("SenetAPI.get_pricing(mock): возвращено %d пакетов", len(result))
            return result

        try:
            resp = httpx.get(
                f"{self._api_url}/pricing",
                headers=self._headers(),
                timeout=10,
            )
            resp.raise_for_status()
            raw = resp.json()
            result = []
            for item in raw.get("packages", []):
                result.append(SenetPricingPackage(
                    zone=item.get("zone", "main"),
                    package_id=item.get("package_id", ""),
                    price=int(item.get("price", 0)),
                    hours=item.get("hours"),
                    fixed_start=item.get("fixed_start"),
                    fixed_end=item.get("fixed_end"),
                    is_popular=bool(item.get("is_popular", False)),
                ))
            return result
        except httpx.HTTPError as exc:
            logger.warning("Senet API недоступен для получения цен: %s", exc)
            raise SenetAPIError(f"Ошибка получения цен Senet: {exc}") from exc

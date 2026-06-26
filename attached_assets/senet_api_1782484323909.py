"""
senet_api.py — адаптер интеграции с CRM Senet.

Архитектура:
  Класс SenetAPI реализует стратегию mock/live через атрибут mode.
  В режиме "mock" все методы возвращают предсказуемые данные для разработки.
  В режиме "live" методы выполняют HTTP-запросы к реальному Senet API.
  Переключение — через SENET_MODE в переменных окружения.

Добавление реального API:
  - Заменить тело методов в блоках `else` (режим live).
  - Не менять сигнатуры методов — хендлеры зависят от них.
"""

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SenetUser:
    """Представление пользователя Senet."""

    user_id: str
    login: str
    phone_last4: Optional[str] = None


class SenetAPIError(Exception):
    """Базовое исключение для ошибок Senet API."""


class SenetAPI:
    """
    Адаптер для работы с Senet CRM.

    Args:
        mode: режим работы — "mock" для разработки, "live" для продакшна.

    Example:
        senet = SenetAPI(mode="mock")
        if senet.user_exists("cyber_stalker"):
            ok = senet.verify_phone_last4("cyber_stalker", "4567")
    """

    # Тестовые данные для mock-режима.
    # Ключ — логин в нижнем регистре, значение — последние 4 цифры телефона.
    _MOCK_USERS: dict[str, str] = {
        "cyber_stalker": "4567",
        "neiro_girl":    "8901",
        "gamer_kz":      "1234",
        "admin":         "0000",
    }

    def __init__(self, mode: str = "mock") -> None:
        if mode not in ("mock", "live"):
            raise ValueError(f"Недопустимый режим Senet API: {mode!r}. Ожидается 'mock' или 'live'.")
        self.mode = mode
        logger.info("SenetAPI инициализирован в режиме: %s", mode)

    # ------------------------------------------------------------------
    # Публичный интерфейс
    # ------------------------------------------------------------------

    def user_exists(self, login: str) -> bool:
        """
        Проверяет, существует ли пользователь в Senet по логину.

        Args:
            login: логин пользователя (регистр не важен).

        Returns:
            True, если пользователь найден в Senet.

        Raises:
            SenetAPIError: при сбое запроса к Senet (только в live-режиме).
        """
        if self.mode == "mock":
            result = login.lower() in self._MOCK_USERS
            logger.debug("SenetAPI.user_exists(mock): login=%r → %s", login, result)
            return result

        # TODO: реализовать HTTP GET /api/user/info?login={login}
        raise NotImplementedError("Senet live API не реализован.")

    def create_user(self, login: str, phone: Optional[str] = None) -> SenetUser:
        """
        Создаёт нового пользователя в Senet.

        Args:
            login: логин нового пользователя.
            phone: номер телефона (опционально).

        Returns:
            SenetUser с данными созданного пользователя.

        Raises:
            SenetAPIError: если пользователь уже существует или ошибка API.
        """
        if self.mode == "mock":
            user = SenetUser(
                user_id=f"senet_{login.lower()}",
                login=login,
                phone_last4=phone[-4:] if phone and len(phone) >= 4 else None,
            )
            logger.debug("SenetAPI.create_user(mock): создан пользователь %r", login)
            return user

        # TODO: реализовать HTTP POST /api/user/create
        raise NotImplementedError("Senet live API не реализован.")

    def verify_phone_last4(self, login: str, phone_last4: str) -> bool:
        """
        Проверяет последние 4 цифры телефона пользователя Senet.

        Args:
            login: логин пользователя в Senet.
            phone_last4: строка из 4 цифр.

        Returns:
            True, если цифры совпадают с телефоном в Senet.

        Raises:
            SenetAPIError: при сбое запроса к Senet (только в live-режиме).
            ValueError: если phone_last4 не состоит из 4 цифр.
        """
        if not (len(phone_last4) == 4 and phone_last4.isdigit()):
            raise ValueError(f"phone_last4 должен быть строкой из 4 цифр, получено: {phone_last4!r}")

        if self.mode == "mock":
            expected = self._MOCK_USERS.get(login.lower())
            result = expected == phone_last4
            logger.debug(
                "SenetAPI.verify_phone_last4(mock): login=%r, input=%r → %s",
                login, phone_last4, result,
            )
            return result

        # TODO: реализовать HTTP POST /api/user/verify_phone
        raise NotImplementedError("Senet live API не реализован.")

    def get_user(self, login: str) -> Optional[SenetUser]:
        """
        Возвращает данные пользователя Senet по логину.

        Args:
            login: логин пользователя.

        Returns:
            SenetUser или None, если пользователь не найден.

        Raises:
            SenetAPIError: при сбое запроса (только в live-режиме).
        """
        if self.mode == "mock":
            if login.lower() not in self._MOCK_USERS:
                return None
            return SenetUser(
                user_id=f"senet_{login.lower()}",
                login=login,
                phone_last4=self._MOCK_USERS[login.lower()],
            )

        # TODO: реализовать HTTP GET /api/user/info?login={login}
        raise NotImplementedError("Senet live API не реализован.")

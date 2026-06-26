"""
db/queries.py — асинхронные SQL-запросы для LORAN.CYBER Bot.

Использует aiosqlite для неблокирующей работы с SQLite из async-хендлеров PTB.

Соглашения:
  - db_path передаётся явно (dependency injection, без глобального состояния).
  - Все функции — async.
  - Возвращаемые строки — aiosqlite.Row (dict-подобный доступ по имени поля).
  - Все SQL-параметры передаются через плейсхолдеры ? — никогда не через f-строки.
  - Входные данные не валидируются здесь — валидация на уровне сервисов/хендлеров.
"""

import logging
from datetime import datetime
from typing import Optional

import aiosqlite

logger = logging.getLogger(__name__)


async def _get_conn(db_path: str) -> aiosqlite.Connection:
    """
    Открывает соединение с SQLite с нужными PRAGMA.

    Args:
        db_path: путь к файлу базы данных.

    Returns:
        aiosqlite.Connection: готовое к использованию соединение.
    """
    conn = await aiosqlite.connect(db_path)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys = ON;")
    await conn.execute("PRAGMA journal_mode = WAL;")
    return conn


# ===========================================================================
# Пользователи
# ===========================================================================


async def get_user(db_path: str, telegram_id: int) -> Optional[aiosqlite.Row]:
    """
    Возвращает запись пользователя по Telegram ID.

    Args:
        db_path: путь к файлу БД.
        telegram_id: числовой ID пользователя в Telegram.

    Returns:
        aiosqlite.Row или None.
    """
    async with await _get_conn(db_path) as conn:
        cursor = await conn.execute(
            "SELECT * FROM users WHERE telegram_id = ?;",
            (telegram_id,),
        )
        return await cursor.fetchone()


async def get_user_by_login(db_path: str, login: str) -> Optional[aiosqlite.Row]:
    """
    Возвращает запись пользователя по логину (case-insensitive).

    Args:
        db_path: путь к файлу БД.
        login: логин пользователя.

    Returns:
        aiosqlite.Row или None.
    """
    async with await _get_conn(db_path) as conn:
        cursor = await conn.execute(
            "SELECT * FROM users WHERE LOWER(login) = LOWER(?);",
            (login,),
        )
        return await cursor.fetchone()


async def get_user_by_senet_login(db_path: str, senet_login: str) -> Optional[aiosqlite.Row]:
    """
    Возвращает пользователя с данным Senet-логином.

    Используется для проверки: не привязан ли Senet-аккаунт к другому telegram_id.

    Args:
        db_path: путь к файлу БД.
        senet_login: логин в системе Senet.

    Returns:
        aiosqlite.Row или None.
    """
    async with await _get_conn(db_path) as conn:
        cursor = await conn.execute(
            "SELECT * FROM users WHERE LOWER(senet_login) = LOWER(?);",
            (senet_login,),
        )
        return await cursor.fetchone()


async def create_user(
    db_path: str,
    telegram_id: int,
    login: str,
    display_name: Optional[str] = None,
    phone: Optional[str] = None,
    is_admin: bool = False,
) -> None:
    """
    Создаёт нового пользователя в таблице users.

    Args:
        db_path: путь к файлу БД.
        telegram_id: Telegram ID пользователя.
        login: логин (уникальный).
        display_name: отображаемое имя (опционально).
        phone: номер телефона (опционально).
        is_admin: флаг администратора.

    Raises:
        aiosqlite.IntegrityError: если telegram_id или login уже заняты.
    """
    async with await _get_conn(db_path) as conn:
        await conn.execute(
            """
            INSERT INTO users (telegram_id, login, display_name, phone, is_admin)
            VALUES (?, ?, ?, ?, ?);
            """,
            (telegram_id, login, display_name, phone, int(is_admin)),
        )
        await conn.commit()


async def update_last_activity(db_path: str, telegram_id: int) -> None:
    """
    Обновляет метку времени последней активности пользователя.

    Args:
        db_path: путь к файлу БД.
        telegram_id: Telegram ID пользователя.
    """
    async with await _get_conn(db_path) as conn:
        await conn.execute(
            "UPDATE users SET last_activity = CURRENT_TIMESTAMP WHERE telegram_id = ?;",
            (telegram_id,),
        )
        await conn.commit()


async def set_user_admin(db_path: str, telegram_id: int) -> None:
    """
    Выдаёт пользователю флаг администратора.

    Если пользователь ещё не существует, создаёт запись с временным логином.

    Args:
        db_path: путь к файлу БД.
        telegram_id: Telegram ID пользователя.
    """
    async with await _get_conn(db_path) as conn:
        await conn.execute(
            """
            INSERT INTO users (telegram_id, login, is_admin)
            VALUES (?, ?, 1)
            ON CONFLICT(telegram_id) DO UPDATE SET is_admin = 1;
            """,
            (telegram_id, f"admin_{telegram_id}"),
        )
        await conn.commit()


# ===========================================================================
# Временные данные регистрации
# ===========================================================================


async def save_pending_registration(
    db_path: str,
    telegram_id: int,
    login: str,
    phone: str,
    display_name: Optional[str],
    expires_at: datetime,
) -> None:
    """
    Сохраняет данные регистрации до завершения верификации Senet.

    Если запись для данного telegram_id уже существует — перезаписывает.

    Args:
        db_path: путь к файлу БД.
        telegram_id: Telegram ID пользователя.
        login: выбранный логин.
        phone: номер телефона (обязателен).
        display_name: отображаемое имя (опционально).
        expires_at: время истечения ожидания верификации (UTC).
    """
    async with await _get_conn(db_path) as conn:
        await conn.execute(
            """
            INSERT INTO pending_registrations (telegram_id, login, display_name, phone, expires_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                login        = excluded.login,
                display_name = excluded.display_name,
                phone        = excluded.phone,
                created_at   = CURRENT_TIMESTAMP,
                expires_at   = excluded.expires_at;
            """,
            (telegram_id, login, display_name, phone, expires_at.isoformat()),
        )
        await conn.commit()


async def get_pending_registration(
    db_path: str,
    telegram_id: int,
) -> Optional[aiosqlite.Row]:
    """
    Возвращает актуальную (не истёкшую) заявку на регистрацию.

    Args:
        db_path: путь к файлу БД.
        telegram_id: Telegram ID пользователя.

    Returns:
        aiosqlite.Row или None, если заявки нет или она истекла.
    """
    async with await _get_conn(db_path) as conn:
        cursor = await conn.execute(
            """
            SELECT * FROM pending_registrations
            WHERE telegram_id = ?
              AND expires_at > CURRENT_TIMESTAMP;
            """,
            (telegram_id,),
        )
        return await cursor.fetchone()


async def clear_pending_registration(db_path: str, telegram_id: int) -> None:
    """
    Удаляет заявку на регистрацию после успешной или неуспешной верификации.

    Args:
        db_path: путь к файлу БД.
        telegram_id: Telegram ID пользователя.
    """
    async with await _get_conn(db_path) as conn:
        await conn.execute(
            "DELETE FROM pending_registrations WHERE telegram_id = ?;",
            (telegram_id,),
        )
        await conn.commit()


# ===========================================================================
# Счётчики верификации Senet
# ===========================================================================


async def increment_senet_verify_attempts(db_path: str, telegram_id: int) -> int:
    """
    Увеличивает счётчик неудачных попыток верификации и возвращает новое значение.

    Args:
        db_path: путь к файлу БД.
        telegram_id: Telegram ID пользователя.

    Returns:
        int: новое значение senet_verify_attempts.
    """
    async with await _get_conn(db_path) as conn:
        await conn.execute(
            """
            UPDATE users
            SET senet_verify_attempts = senet_verify_attempts + 1
            WHERE telegram_id = ?;
            """,
            (telegram_id,),
        )
        await conn.commit()
        cursor = await conn.execute(
            "SELECT senet_verify_attempts FROM users WHERE telegram_id = ?;",
            (telegram_id,),
        )
        row = await cursor.fetchone()
        return row["senet_verify_attempts"] if row else 0


async def lock_senet_verification(
    db_path: str,
    telegram_id: int,
    locked_until: datetime,
) -> None:
    """
    Устанавливает временную блокировку верификации.

    Args:
        db_path: путь к файлу БД.
        telegram_id: Telegram ID пользователя.
        locked_until: время снятия блокировки (UTC).
    """
    async with await _get_conn(db_path) as conn:
        await conn.execute(
            """
            UPDATE users
            SET senet_verify_locked_until = ?
            WHERE telegram_id = ?;
            """,
            (locked_until.isoformat(), telegram_id),
        )
        await conn.commit()


async def reset_senet_verify_attempts(db_path: str, telegram_id: int) -> None:
    """
    Сбрасывает счётчик и блокировку верификации после успеха.

    Args:
        db_path: путь к файлу БД.
        telegram_id: Telegram ID пользователя.
    """
    async with await _get_conn(db_path) as conn:
        await conn.execute(
            """
            UPDATE users
            SET senet_verify_attempts     = 0,
                senet_verify_locked_until = NULL
            WHERE telegram_id = ?;
            """,
            (telegram_id,),
        )
        await conn.commit()


async def link_senet_user(
    db_path: str,
    telegram_id: int,
    senet_user_id: str,
    senet_login: str,
) -> None:
    """
    Привязывает Senet-аккаунт к пользователю и выставляет senet_verified = 1.

    Args:
        db_path: путь к файлу БД.
        telegram_id: Telegram ID пользователя.
        senet_user_id: внутренний ID пользователя в Senet.
        senet_login: логин пользователя в Senet.
    """
    async with await _get_conn(db_path) as conn:
        await conn.execute(
            """
            UPDATE users
            SET senet_user_id = ?,
                senet_login   = ?,
                senet_verified = 1
            WHERE telegram_id = ?;
            """,
            (senet_user_id, senet_login, telegram_id),
        )
        await conn.commit()


# ===========================================================================
# Коды верификации для стойки
# ===========================================================================


async def save_verification_code(
    db_path: str,
    telegram_id: int,
    senet_login: str,
    code: str,
    expires_at: datetime,
) -> None:
    """
    Сохраняет код верификации для стойки.

    Перед созданием нового кода инвалидирует все активные коды
    для данной пары (telegram_id, senet_login).

    Args:
        db_path: путь к файлу БД.
        telegram_id: Telegram ID пользователя.
        senet_login: логин в Senet, к которому привязывается код.
        code: сгенерированный код.
        expires_at: время истечения (UTC).
    """
    async with await _get_conn(db_path) as conn:
        await conn.execute(
            """
            UPDATE verification_codes
            SET status = 'expired'
            WHERE telegram_id = ? AND LOWER(senet_login) = LOWER(?) AND status = 'pending';
            """,
            (telegram_id, senet_login),
        )
        await conn.execute(
            """
            INSERT INTO verification_codes (code, telegram_id, senet_login, status, expires_at)
            VALUES (?, ?, ?, 'pending', ?);
            """,
            (code, telegram_id, senet_login, expires_at.isoformat()),
        )
        await conn.commit()


async def get_pending_verification(
    db_path: str,
    telegram_id: int,
    senet_login: str,
) -> Optional[aiosqlite.Row]:
    """
    Возвращает активный (не истёкший) код верификации.

    Args:
        db_path: путь к файлу БД.
        telegram_id: Telegram ID пользователя.
        senet_login: логин в Senet.

    Returns:
        aiosqlite.Row или None.
    """
    async with await _get_conn(db_path) as conn:
        cursor = await conn.execute(
            """
            SELECT * FROM verification_codes
            WHERE telegram_id = ?
              AND LOWER(senet_login) = LOWER(?)
              AND status = 'pending'
              AND expires_at > CURRENT_TIMESTAMP
            ORDER BY created_at DESC
            LIMIT 1;
            """,
            (telegram_id, senet_login),
        )
        return await cursor.fetchone()


async def mark_verification_code_used(db_path: str, code: str) -> None:
    """
    Помечает код верификации как использованный.

    Args:
        db_path: путь к файлу БД.
        code: строка кода.
    """
    async with await _get_conn(db_path) as conn:
        await conn.execute(
            """
            UPDATE verification_codes
            SET status = 'used', used_at = CURRENT_TIMESTAMP
            WHERE code = ?;
            """,
            (code,),
        )
        await conn.commit()


# ===========================================================================
# Бронирования
# ===========================================================================


async def get_active_bookings(
    db_path: str,
    telegram_id: int,
) -> list[aiosqlite.Row]:
    """
    Возвращает все активные (confirmed) бронирования пользователя.

    Args:
        db_path: путь к файлу БД.
        telegram_id: Telegram ID пользователя.

    Returns:
        Список aiosqlite.Row, отсортированный по дате и времени начала.
    """
    async with await _get_conn(db_path) as conn:
        cursor = await conn.execute(
            """
            SELECT * FROM bookings
            WHERE telegram_id = ? AND status = 'confirmed'
            ORDER BY date, time_from;
            """,
            (telegram_id,),
        )
        return await cursor.fetchall()


async def get_booking_by_id(
    db_path: str,
    booking_id: int,
) -> Optional[aiosqlite.Row]:
    """
    Возвращает бронирование по его ID.

    Args:
        db_path: путь к файлу БД.
        booking_id: ID записи в таблице bookings.

    Returns:
        aiosqlite.Row или None.
    """
    async with await _get_conn(db_path) as conn:
        cursor = await conn.execute(
            "SELECT * FROM bookings WHERE id = ?;",
            (booking_id,),
        )
        return await cursor.fetchone()


async def get_booking_pcs(
    db_path: str,
    booking_id: int,
) -> list[aiosqlite.Row]:
    """
    Возвращает список ПК для бронирования.

    Args:
        db_path: путь к файлу БД.
        booking_id: ID записи в таблице bookings.

    Returns:
        Список aiosqlite.Row из таблицы booking_pcs.
    """
    async with await _get_conn(db_path) as conn:
        cursor = await conn.execute(
            "SELECT * FROM booking_pcs WHERE booking_id = ? ORDER BY pc_id;",
            (booking_id,),
        )
        return await cursor.fetchall()


async def count_active_pcs(db_path: str, telegram_id: int) -> int:
    """
    Считает количество ПК во всех активных бронированиях пользователя.

    Используется для проверки лимита: не более 5 ПК суммарно.

    Args:
        db_path: путь к файлу БД.
        telegram_id: Telegram ID пользователя.

    Returns:
        int: суммарное количество ПК в активных бронированиях.
    """
    async with await _get_conn(db_path) as conn:
        cursor = await conn.execute(
            """
            SELECT COUNT(bp.pc_id) AS total
            FROM booking_pcs bp
            JOIN bookings b ON b.id = bp.booking_id
            WHERE b.telegram_id = ? AND b.status = 'confirmed';
            """,
            (telegram_id,),
        )
        row = await cursor.fetchone()
        return row["total"] if row else 0


async def get_conflicting_bookings(
    db_path: str,
    pc_ids: list[int],
    date: str,
    time_from: str,
    time_to: str,
) -> list[aiosqlite.Row]:
    """
    Возвращает активные бронирования, пересекающиеся по времени с запрошенными ПК.

    Конфликт: существующая бронь перекрывает интервал [time_from, time_to).
    Условие: existing.time_from < time_to AND existing.time_to > time_from.

    Args:
        db_path: путь к файлу БД.
        pc_ids: список ID запрашиваемых ПК.
        date: дата в формате YYYY-MM-DD.
        time_from: начало в формате HH:MM.
        time_to: конец в формате HH:MM.

    Returns:
        Список конфликтующих записей из bookings.
    """
    if not pc_ids:
        return []

    # Строим плейсхолдеры для IN-клаузы динамически, но значения — через параметры.
    placeholders = ",".join("?" * len(pc_ids))

    async with await _get_conn(db_path) as conn:
        cursor = await conn.execute(
            f"""
            SELECT DISTINCT b.*
            FROM bookings b
            JOIN booking_pcs bp ON bp.booking_id = b.id
            WHERE b.status = 'confirmed'
              AND b.date = ?
              AND b.time_from < ?
              AND b.time_to > ?
              AND bp.pc_id IN ({placeholders});
            """,
            (date, time_to, time_from, *pc_ids),
        )
        return await cursor.fetchall()


async def create_booking(
    db_path: str,
    telegram_id: int,
    booking_code: str,
    date: str,
    time_from: str,
    time_to: str,
    total_price: int,
) -> int:
    """
    Создаёт запись бронирования и возвращает её ID.

    Args:
        db_path: путь к файлу БД.
        telegram_id: Telegram ID пользователя.
        booking_code: уникальный код брони.
        date: дата в формате YYYY-MM-DD.
        time_from: начало в формате HH:MM.
        time_to: конец в формате HH:MM.
        total_price: итоговая цена в тенге.

    Returns:
        int: ID созданной записи.

    Raises:
        aiosqlite.IntegrityError: если booking_code уже существует.
    """
    async with await _get_conn(db_path) as conn:
        cursor = await conn.execute(
            """
            INSERT INTO bookings (telegram_id, booking_code, date, time_from, time_to, total_price)
            VALUES (?, ?, ?, ?, ?, ?);
            """,
            (telegram_id, booking_code, date, time_from, time_to, total_price),
        )
        await conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]


async def add_booking_pc(
    db_path: str,
    booking_id: int,
    pc_id: int,
    zone: str,
    price_per_pc: int,
) -> None:
    """
    Добавляет ПК к бронированию.

    Args:
        db_path: путь к файлу БД.
        booking_id: ID бронирования.
        pc_id: номер ПК (1–23).
        zone: зона — "MAIN" или "BOOTCAMP".
        price_per_pc: цена за один ПК в тенге.
    """
    async with await _get_conn(db_path) as conn:
        await conn.execute(
            """
            INSERT INTO booking_pcs (booking_id, pc_id, zone, price_per_pc)
            VALUES (?, ?, ?, ?);
            """,
            (booking_id, pc_id, zone.upper(), price_per_pc),
        )
        await conn.commit()


async def cancel_booking(
    db_path: str,
    booking_id: int,
    cancelled_by: str,
    cancel_reason: Optional[str] = None,
) -> None:
    """
    Отменяет бронирование, выставляя статус 'cancelled'.

    Args:
        db_path: путь к файлу БД.
        booking_id: ID бронирования.
        cancelled_by: "user" или "admin".
        cancel_reason: текстовая причина отмены (опционально).
    """
    async with await _get_conn(db_path) as conn:
        await conn.execute(
            """
            UPDATE bookings
            SET status       = 'cancelled',
                cancelled_at = CURRENT_TIMESTAMP,
                cancelled_by = ?,
                cancel_reason = ?
            WHERE id = ?;
            """,
            (cancelled_by, cancel_reason, booking_id),
        )
        await conn.commit()


async def get_booking_history(
    db_path: str,
    telegram_id: int,
    limit: int = 10,
) -> list[aiosqlite.Row]:
    """
    Возвращает историю бронирований пользователя (все статусы).

    Args:
        db_path: путь к файлу БД.
        telegram_id: Telegram ID пользователя.
        limit: максимальное количество записей.

    Returns:
        Список aiosqlite.Row, отсортированный по дате создания (новые первыми).
    """
    async with await _get_conn(db_path) as conn:
        cursor = await conn.execute(
            """
            SELECT * FROM bookings
            WHERE telegram_id = ?
            ORDER BY created_at DESC
            LIMIT ?;
            """,
            (telegram_id, limit),
        )
        return await cursor.fetchall()


async def update_booking_code(db_path: str, booking_id: int, booking_code: str) -> None:
    """
    Обновляет код бронирования после получения финального ID записи.

    Вызывается сразу после create_booking() — временный код заменяется
    финальным, включающим реальный ID строки.

    Args:
        db_path: путь к файлу БД.
        booking_id: ID записи в таблице bookings.
        booking_code: финальный код брони (напр. "LOR-42-K7N").
    """
    async with await _get_conn(db_path) as conn:
        await conn.execute(
            "UPDATE bookings SET booking_code = ? WHERE id = ?;",
            (booking_code, booking_id),
        )
        await conn.commit()

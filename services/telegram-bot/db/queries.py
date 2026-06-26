"""
db/queries.py — асинхронные SQL-запросы для LORAN.CYBER Bot.
"""

import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import AsyncIterator, Optional

import aiosqlite

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _db(db_path: str) -> AsyncIterator[aiosqlite.Connection]:
    """Контекстный менеджер: открывает соединение, настраивает прагмы, закрывает."""
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA foreign_keys = ON;")
        await conn.execute("PRAGMA journal_mode = WAL;")
        yield conn


# ===========================================================================
# Пользователи
# ===========================================================================

async def get_user(db_path: str, telegram_id: int) -> Optional[aiosqlite.Row]:
    async with _db(db_path) as conn:
        cursor = await conn.execute(
            "SELECT * FROM users WHERE telegram_id = ?;", (telegram_id,)
        )
        return await cursor.fetchone()


async def get_user_by_login(db_path: str, login: str) -> Optional[aiosqlite.Row]:
    async with _db(db_path) as conn:
        cursor = await conn.execute(
            "SELECT * FROM users WHERE LOWER(login) = LOWER(?);", (login,)
        )
        return await cursor.fetchone()


async def get_user_by_senet_login(db_path: str, senet_login: str) -> Optional[aiosqlite.Row]:
    async with _db(db_path) as conn:
        cursor = await conn.execute(
            "SELECT * FROM users WHERE LOWER(senet_login) = LOWER(?);", (senet_login,)
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
    async with _db(db_path) as conn:
        await conn.execute(
            """
            INSERT INTO users (telegram_id, login, display_name, phone, is_admin)
            VALUES (?, ?, ?, ?, ?);
            """,
            (telegram_id, login, display_name, phone, int(is_admin)),
        )
        await conn.commit()


async def update_last_activity(db_path: str, telegram_id: int) -> None:
    async with _db(db_path) as conn:
        await conn.execute(
            "UPDATE users SET last_activity = CURRENT_TIMESTAMP WHERE telegram_id = ?;",
            (telegram_id,),
        )
        await conn.commit()


async def set_user_admin(db_path: str, telegram_id: int) -> None:
    async with _db(db_path) as conn:
        await conn.execute(
            """
            INSERT INTO users (telegram_id, login, is_admin)
            VALUES (?, ?, 1)
            ON CONFLICT(telegram_id) DO UPDATE SET is_admin = 1;
            """,
            (telegram_id, f"admin_{telegram_id}"),
        )
        await conn.commit()


async def ban_user(db_path: str, telegram_id: int, reason: str, admin_id: int) -> None:
    async with _db(db_path) as conn:
        await conn.execute(
            "UPDATE users SET status='banned', banned_reason=? WHERE telegram_id=?;",
            (reason, telegram_id),
        )
        await conn.commit()


async def unban_user(db_path: str, telegram_id: int) -> None:
    async with _db(db_path) as conn:
        await conn.execute(
            "UPDATE users SET status='active', banned_reason=NULL WHERE telegram_id=?;",
            (telegram_id,),
        )
        await conn.commit()


async def get_all_users(db_path: str, limit: int = 50, offset: int = 0) -> list:
    async with _db(db_path) as conn:
        cursor = await conn.execute(
            "SELECT * FROM users ORDER BY registered_at DESC LIMIT ? OFFSET ?;",
            (limit, offset),
        )
        return await cursor.fetchall()


async def search_users(db_path: str, query: str, limit: int = 20) -> list:
    like = f"%{query}%"
    async with _db(db_path) as conn:
        cursor = await conn.execute(
            """
            SELECT * FROM users
            WHERE login LIKE ? OR phone LIKE ? OR senet_login LIKE ?
            ORDER BY registered_at DESC LIMIT ?;
            """,
            (like, like, like, limit),
        )
        return await cursor.fetchall()


async def count_users(db_path: str) -> int:
    async with _db(db_path) as conn:
        cursor = await conn.execute("SELECT COUNT(*) FROM users;")
        row = await cursor.fetchone()
        return row[0] if row else 0


# ===========================================================================
# Pending registrations
# ===========================================================================

async def save_pending_registration(
    db_path: str,
    telegram_id: int,
    login: str,
    phone: str,
    display_name: Optional[str],
    expires_at: datetime,
) -> None:
    async with _db(db_path) as conn:
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


async def get_pending_registration(db_path: str, telegram_id: int) -> Optional[aiosqlite.Row]:
    async with _db(db_path) as conn:
        cursor = await conn.execute(
            """
            SELECT * FROM pending_registrations
            WHERE telegram_id = ? AND expires_at > CURRENT_TIMESTAMP;
            """,
            (telegram_id,),
        )
        return await cursor.fetchone()


async def clear_pending_registration(db_path: str, telegram_id: int) -> None:
    async with _db(db_path) as conn:
        await conn.execute(
            "DELETE FROM pending_registrations WHERE telegram_id = ?;", (telegram_id,)
        )
        await conn.commit()


# ===========================================================================
# Верификация Senet
# ===========================================================================

async def increment_senet_verify_attempts(db_path: str, telegram_id: int) -> int:
    async with _db(db_path) as conn:
        await conn.execute(
            "UPDATE users SET senet_verify_attempts = senet_verify_attempts + 1 WHERE telegram_id = ?;",
            (telegram_id,),
        )
        await conn.commit()
        cursor = await conn.execute(
            "SELECT senet_verify_attempts FROM users WHERE telegram_id = ?;",
            (telegram_id,),
        )
        row = await cursor.fetchone()
        return row["senet_verify_attempts"] if row else 0


async def lock_senet_verification(db_path: str, telegram_id: int, locked_until: datetime) -> None:
    async with _db(db_path) as conn:
        await conn.execute(
            "UPDATE users SET senet_verify_locked_until = ? WHERE telegram_id = ?;",
            (locked_until.isoformat(), telegram_id),
        )
        await conn.commit()


async def reset_senet_verify_attempts(db_path: str, telegram_id: int) -> None:
    async with _db(db_path) as conn:
        await conn.execute(
            """
            UPDATE users
            SET senet_verify_attempts = 0, senet_verify_locked_until = NULL
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
    async with _db(db_path) as conn:
        await conn.execute(
            """
            UPDATE users
            SET senet_user_id = ?, senet_login = ?, senet_verified = 1
            WHERE telegram_id = ?;
            """,
            (senet_user_id, senet_login, telegram_id),
        )
        await conn.commit()


# ===========================================================================
# Коды верификации
# ===========================================================================

async def save_verification_code(
    db_path: str,
    code: str,
    telegram_id: int,
    senet_login: str,
    expires_at: datetime,
) -> None:
    async with _db(db_path) as conn:
        await conn.execute(
            """
            INSERT INTO verification_codes (code, telegram_id, senet_login, expires_at)
            VALUES (?, ?, ?, ?);
            """,
            (code, telegram_id, senet_login, expires_at.isoformat()),
        )
        await conn.commit()


async def get_pending_verification_codes(db_path: str) -> list:
    async with _db(db_path) as conn:
        cursor = await conn.execute(
            """
            SELECT vc.*, u.login FROM verification_codes vc
            LEFT JOIN users u ON vc.telegram_id = u.telegram_id
            WHERE vc.status = 'pending' AND vc.expires_at > CURRENT_TIMESTAMP
            ORDER BY vc.created_at DESC;
            """
        )
        return await cursor.fetchall()


async def mark_verification_code_used(db_path: str, code: str) -> None:
    async with _db(db_path) as conn:
        await conn.execute(
            """
            UPDATE verification_codes
            SET status = 'used', used_at = CURRENT_TIMESTAMP
            WHERE code = ?;
            """,
            (code,),
        )
        await conn.commit()


async def reject_verification_code(db_path: str, code: str) -> None:
    async with _db(db_path) as conn:
        await conn.execute(
            "UPDATE verification_codes SET status='rejected' WHERE code=?;",
            (code,),
        )
        await conn.commit()


# ===========================================================================
# Бронирования
# ===========================================================================

async def create_booking(
    db_path: str,
    telegram_id: int,
    date: str,
    time_from: str,
    time_to: str,
    total_price: int,
) -> int:
    async with _db(db_path) as conn:
        cursor = await conn.execute(
            """
            INSERT INTO bookings (telegram_id, date, time_from, time_to, total_price)
            VALUES (?, ?, ?, ?, ?);
            """,
            (telegram_id, date, time_from, time_to, total_price),
        )
        await conn.commit()
        return cursor.lastrowid


async def update_booking_code(db_path: str, booking_id: int, code: str) -> None:
    async with _db(db_path) as conn:
        await conn.execute(
            "UPDATE bookings SET booking_code = ? WHERE id = ?;",
            (code, booking_id),
        )
        await conn.commit()


async def add_booking_pc(
    db_path: str,
    booking_id: int,
    pc_id: int,
    zone: str,
    price_per_pc: int,
) -> None:
    async with _db(db_path) as conn:
        await conn.execute(
            "INSERT INTO booking_pcs (booking_id, pc_id, zone, price_per_pc) VALUES (?, ?, ?, ?);",
            (booking_id, pc_id, zone, price_per_pc),
        )
        await conn.commit()


async def get_booking_by_code(db_path: str, code: str) -> Optional[aiosqlite.Row]:
    async with _db(db_path) as conn:
        cursor = await conn.execute(
            "SELECT * FROM bookings WHERE booking_code = ?;", (code,)
        )
        return await cursor.fetchone()


async def cancel_booking(
    db_path: str,
    booking_id: int,
    cancelled_by: str,
    reason: str = "",
) -> None:
    async with _db(db_path) as conn:
        await conn.execute(
            """
            UPDATE bookings
            SET status='cancelled', cancelled_at=CURRENT_TIMESTAMP,
                cancelled_by=?, cancel_reason=?
            WHERE id=?;
            """,
            (cancelled_by, reason, booking_id),
        )
        await conn.commit()


async def get_bookings_for_user(db_path: str, telegram_id: int, status: str = "confirmed") -> list:
    async with _db(db_path) as conn:
        cursor = await conn.execute(
            """
            SELECT b.*, GROUP_CONCAT(bp.pc_id) as pc_list
            FROM bookings b
            LEFT JOIN booking_pcs bp ON b.id = bp.booking_id
            WHERE b.telegram_id = ? AND b.status = ?
            GROUP BY b.id
            ORDER BY b.date, b.time_from;
            """,
            (telegram_id, status),
        )
        return await cursor.fetchall()


async def count_active_pcs(db_path: str, telegram_id: int) -> int:
    async with _db(db_path) as conn:
        cursor = await conn.execute(
            """
            SELECT COUNT(bp.pc_id) FROM bookings b
            JOIN booking_pcs bp ON b.id = bp.booking_id
            WHERE b.telegram_id = ? AND b.status = 'confirmed';
            """,
            (telegram_id,),
        )
        row = await cursor.fetchone()
        return row[0] if row else 0


async def check_pc_conflicts(
    db_path: str,
    date: str,
    time_from: str,
    time_to: str,
    pc_ids: list[int],
) -> list[int]:
    """Возвращает список PC ID, занятых на указанное время."""
    if not pc_ids:
        return []
    placeholders = ",".join("?" * len(pc_ids))
    async with _db(db_path) as conn:
        cursor = await conn.execute(
            f"""
            SELECT DISTINCT bp.pc_id FROM bookings b
            JOIN booking_pcs bp ON b.id = bp.booking_id
            WHERE b.status = 'confirmed'
              AND b.date = ?
              AND b.time_from < ?
              AND b.time_to > ?
              AND bp.pc_id IN ({placeholders});
            """,
            [date, time_to, time_from] + pc_ids,
        )
        rows = await cursor.fetchall()
        return [row[0] for row in rows]


async def get_all_bookings(
    db_path: str,
    date_filter: Optional[str] = None,
    status_filter: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> list:
    conditions = []
    params: list = []
    if date_filter:
        conditions.append("b.date = ?")
        params.append(date_filter)
    if status_filter:
        conditions.append("b.status = ?")
        params.append(status_filter)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params += [limit, offset]

    async with _db(db_path) as conn:
        cursor = await conn.execute(
            f"""
            SELECT b.*, u.login, u.phone,
                   GROUP_CONCAT(bp.pc_id) as pc_list
            FROM bookings b
            LEFT JOIN users u ON b.telegram_id = u.telegram_id
            LEFT JOIN booking_pcs bp ON b.id = bp.booking_id
            {where}
            GROUP BY b.id
            ORDER BY b.date DESC, b.time_from DESC
            LIMIT ? OFFSET ?;
            """,
            params,
        )
        return await cursor.fetchall()


async def get_todays_stats(db_path: str) -> dict:
    """Статистика для дашборда — брони на сегодня, выручка, загрузка ПК."""
    async with _db(db_path) as conn:
        cur = await conn.execute(
            """
            SELECT COUNT(*) as booking_count,
                   COALESCE(SUM(total_price), 0) as revenue,
                   COUNT(DISTINCT b.telegram_id) as unique_clients
            FROM bookings b
            WHERE b.date = DATE('now') AND b.status = 'confirmed';
            """
        )
        row = await cur.fetchone()
        stats = dict(row) if row else {}

        cur2 = await conn.execute(
            """
            SELECT COUNT(DISTINCT bp.pc_id) as booked_pcs
            FROM bookings b JOIN booking_pcs bp ON b.id = bp.booking_id
            WHERE b.date = DATE('now') AND b.status = 'confirmed'
              AND b.time_from <= TIME('now') AND b.time_to > TIME('now');
            """
        )
        row2 = await cur2.fetchone()
        if row2:
            stats["booked_pcs_now"] = row2["booked_pcs"]

        return stats


async def get_bookings_count_by_day(db_path: str, days: int = 7) -> list:
    async with _db(db_path) as conn:
        cursor = await conn.execute(
            """
            SELECT date, COUNT(*) as count, COALESCE(SUM(total_price), 0) as revenue
            FROM bookings
            WHERE date >= DATE('now', ? || ' days') AND status = 'confirmed'
            GROUP BY date ORDER BY date;
            """,
            (f"-{days}",),
        )
        return await cursor.fetchall()


async def get_top_clients(db_path: str, limit: int = 10) -> list:
    async with _db(db_path) as conn:
        cursor = await conn.execute(
            """
            SELECT u.login, u.phone, COUNT(b.id) as booking_count,
                   COALESCE(SUM(b.total_price), 0) as total_spent
            FROM bookings b JOIN users u ON b.telegram_id = u.telegram_id
            WHERE b.status = 'confirmed'
            GROUP BY b.telegram_id ORDER BY booking_count DESC LIMIT ?;
            """,
            (limit,),
        )
        return await cursor.fetchall()


async def get_top_pcs(db_path: str, limit: int = 10) -> list:
    async with _db(db_path) as conn:
        cursor = await conn.execute(
            """
            SELECT bp.pc_id, bp.zone, COUNT(*) as usage_count
            FROM booking_pcs bp JOIN bookings b ON bp.booking_id = b.id
            WHERE b.status = 'confirmed'
            GROUP BY bp.pc_id ORDER BY usage_count DESC LIMIT ?;
            """,
            (limit,),
        )
        return await cursor.fetchall()


# ===========================================================================
# Цены (Этап 5 — загрузка из Senet, кэш в БД)
# ===========================================================================

async def upsert_pricing(
    db_path: str,
    zone: str,
    package_id: str,
    price: int,
    hours: Optional[int] = None,
    fixed_start: Optional[str] = None,
    fixed_end: Optional[str] = None,
    is_popular: bool = False,
) -> None:
    async with _db(db_path) as conn:
        await conn.execute(
            """
            INSERT INTO pricing (zone, package_id, price, hours, fixed_start, fixed_end, is_popular, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(zone, package_id) DO UPDATE SET
                price=excluded.price, hours=excluded.hours,
                fixed_start=excluded.fixed_start, fixed_end=excluded.fixed_end,
                is_popular=excluded.is_popular, updated_at=CURRENT_TIMESTAMP;
            """,
            (zone, package_id, price, hours, fixed_start, fixed_end, int(is_popular)),
        )
        await conn.commit()


async def get_all_pricing(db_path: str) -> list:
    async with _db(db_path) as conn:
        cursor = await conn.execute(
            "SELECT * FROM pricing WHERE is_active=1 ORDER BY zone, package_id;"
        )
        return await cursor.fetchall()


async def get_price(db_path: str, zone: str, package_id: str) -> Optional[int]:
    async with _db(db_path) as conn:
        cursor = await conn.execute(
            "SELECT price FROM pricing WHERE zone=? AND package_id=? AND is_active=1;",
            (zone, package_id),
        )
        row = await cursor.fetchone()
        return row["price"] if row else None


# ===========================================================================
# Контакты клуба (Этап 5)
# ===========================================================================

async def get_club_contacts(db_path: str) -> Optional[aiosqlite.Row]:
    async with _db(db_path) as conn:
        cursor = await conn.execute("SELECT * FROM club_contacts WHERE id=1;")
        return await cursor.fetchone()


async def update_club_contacts(
    db_path: str,
    phone: Optional[str] = None,
    whatsapp: Optional[str] = None,
    telegram: Optional[str] = None,
    instagram: Optional[str] = None,
    updated_by: Optional[int] = None,
) -> None:
    async with _db(db_path) as conn:
        await conn.execute(
            """
            UPDATE club_contacts
            SET phone=COALESCE(?, phone),
                whatsapp=COALESCE(?, whatsapp),
                telegram=COALESCE(?, telegram),
                instagram=COALESCE(?, instagram),
                updated_at=CURRENT_TIMESTAMP,
                updated_by=COALESCE(?, updated_by)
            WHERE id=1;
            """,
            (phone, whatsapp, telegram, instagram, updated_by),
        )
        await conn.commit()


# ===========================================================================
# Журнал админ-действий
# ===========================================================================

async def log_admin_action(
    db_path: str,
    admin_id: int,
    action: str,
    target_type: str = "",
    target_id: str = "",
    details: str = "",
) -> None:
    async with _db(db_path) as conn:
        await conn.execute(
            """
            INSERT INTO admin_logs (admin_id, action, target_type, target_id, details)
            VALUES (?, ?, ?, ?, ?);
            """,
            (admin_id, action, target_type, target_id, details),
        )
        await conn.commit()


async def get_admin_logs(db_path: str, limit: int = 50) -> list:
    async with _db(db_path) as conn:
        cursor = await conn.execute(
            """
            SELECT al.*, u.login as admin_login
            FROM admin_logs al
            LEFT JOIN users u ON al.admin_id = u.telegram_id
            ORDER BY al.created_at DESC LIMIT ?;
            """,
            (limit,),
        )
        return await cursor.fetchall()


# ===========================================================================
# Администраторы — авторизация (Этап 5)
# ===========================================================================

async def set_admin_credentials(
    db_path: str,
    telegram_id: int,
    admin_login: str,
    password_hash: str,
    admin_level: str = "regular",
) -> None:
    async with _db(db_path) as conn:
        await conn.execute(
            """
            UPDATE users
            SET admin_login=?, admin_password_hash=?, admin_level=?, is_admin=1
            WHERE telegram_id=?;
            """,
            (admin_login, password_hash, admin_level, telegram_id),
        )
        await conn.commit()


async def get_admin_by_login(db_path: str, admin_login: str) -> Optional[aiosqlite.Row]:
    async with _db(db_path) as conn:
        cursor = await conn.execute(
            "SELECT * FROM users WHERE LOWER(admin_login)=LOWER(?) AND is_admin=1;",
            (admin_login,),
        )
        return await cursor.fetchone()


async def increment_admin_login_attempts(db_path: str, telegram_id: int) -> int:
    async with _db(db_path) as conn:
        await conn.execute(
            "UPDATE users SET admin_login_attempts=admin_login_attempts+1 WHERE telegram_id=?;",
            (telegram_id,),
        )
        await conn.commit()
        cursor = await conn.execute(
            "SELECT admin_login_attempts FROM users WHERE telegram_id=?;",
            (telegram_id,),
        )
        row = await cursor.fetchone()
        return row["admin_login_attempts"] if row else 0


async def lock_admin_login(db_path: str, telegram_id: int, locked_until: datetime) -> None:
    async with _db(db_path) as conn:
        await conn.execute(
            "UPDATE users SET admin_locked_until=? WHERE telegram_id=?;",
            (locked_until.isoformat(), telegram_id),
        )
        await conn.commit()


async def reset_admin_login_attempts(db_path: str, telegram_id: int) -> None:
    async with _db(db_path) as conn:
        await conn.execute(
            "UPDATE users SET admin_login_attempts=0, admin_locked_until=NULL WHERE telegram_id=?;",
            (telegram_id,),
        )
        await conn.commit()


# ===========================================================================
# Совместимость: оставляем _get_conn для handlers/admin.py (_count_admins_with_login)
# ===========================================================================

async def _get_conn(db_path: str):
    """
    Устаревший хелпер. Используйте контекстный менеджер _db() напрямую.
    Оставлен только для обратной совместимости с inline-запросами в handlers/admin.py.
    """
    conn = aiosqlite.connect(db_path)
    return conn

"""
db/queries_posts.py — CRUD для posts (новости/акции) и broadcasts (рассылки).

Этап 6.
"""

import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import AsyncIterator, Optional

import aiosqlite

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _db(db_path: str) -> AsyncIterator[aiosqlite.Connection]:
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA foreign_keys = ON;")
        await conn.execute("PRAGMA journal_mode = WAL;")
        yield conn


# ===========================================================================
# Posts
# ===========================================================================

async def create_post(
    db_path: str,
    admin_id: int,
    post_type: str,
    title: str,
    body: str,
    image_url: Optional[str] = None,
    button_text: Optional[str] = None,
    button_action: Optional[str] = None,
    promo_code: Optional[str] = None,
    starts_at: Optional[str] = None,
    expires_at: Optional[str] = None,
    status: str = "draft",
) -> int:
    async with _db(db_path) as conn:
        cursor = await conn.execute(
            """
            INSERT INTO posts
                (type, title, body, image_url, button_text, button_action,
                 promo_code, starts_at, expires_at, status, admin_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (post_type, title, body, image_url, button_text, button_action,
             promo_code, starts_at, expires_at, status, admin_id),
        )
        await conn.commit()
        return cursor.lastrowid


async def get_post(db_path: str, post_id: int) -> Optional[aiosqlite.Row]:
    async with _db(db_path) as conn:
        cursor = await conn.execute(
            "SELECT * FROM posts WHERE id = ?;", (post_id,)
        )
        return await cursor.fetchone()


async def list_posts(
    db_path: str,
    status: Optional[str] = None,
    limit: int = 10,
    offset: int = 0,
) -> list:
    async with _db(db_path) as conn:
        if status:
            cursor = await conn.execute(
                """
                SELECT p.*, u.login as author_login
                FROM posts p LEFT JOIN users u ON p.admin_id = u.telegram_id
                WHERE p.status = ?
                ORDER BY p.created_at DESC LIMIT ? OFFSET ?;
                """,
                (status, limit, offset),
            )
        else:
            cursor = await conn.execute(
                """
                SELECT p.*, u.login as author_login
                FROM posts p LEFT JOIN users u ON p.admin_id = u.telegram_id
                WHERE p.status != 'deleted'
                ORDER BY p.created_at DESC LIMIT ? OFFSET ?;
                """,
                (limit, offset),
            )
        return await cursor.fetchall()


async def update_post(
    db_path: str,
    post_id: int,
    **fields,
) -> None:
    allowed = {
        "title", "body", "image_url", "button_text", "button_action",
        "promo_code", "starts_at", "expires_at", "status",
    }
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    set_clause = ", ".join(f"{k}=?" for k in updates)
    values = list(updates.values()) + [post_id]
    async with _db(db_path) as conn:
        await conn.execute(
            f"UPDATE posts SET {set_clause} WHERE id=?;", values
        )
        await conn.commit()


async def publish_post(db_path: str, post_id: int) -> None:
    await update_post(db_path, post_id, status="published")


async def delete_post(db_path: str, post_id: int) -> None:
    await update_post(db_path, post_id, status="deleted")


# ===========================================================================
# Recipients resolver
# ===========================================================================

async def get_recipients(db_path: str, target_group: str) -> list[aiosqlite.Row]:
    """
    Возвращает список пользователей для рассылки.

    target_group:
        all       — все active пользователи
        active    — бронировали за последние 30 дней
        bootcamp  — хоть раз бронировали ПК из BOOTCAMP-зоны (19–23)
        newbie    — менее 3 броней суммарно
    """
    async with _db(db_path) as conn:
        if target_group == "all":
            cursor = await conn.execute(
                "SELECT telegram_id FROM users WHERE status='active';"
            )
        elif target_group == "active":
            cursor = await conn.execute(
                """
                SELECT DISTINCT u.telegram_id
                FROM users u
                JOIN bookings b ON b.telegram_id = u.telegram_id
                WHERE u.status = 'active'
                  AND b.status = 'confirmed'
                  AND b.date >= DATE('now', '-30 days');
                """
            )
        elif target_group == "bootcamp":
            cursor = await conn.execute(
                """
                SELECT DISTINCT u.telegram_id
                FROM users u
                JOIN bookings b ON b.telegram_id = u.telegram_id
                JOIN booking_pcs bp ON bp.booking_id = b.id
                WHERE u.status = 'active'
                  AND b.status = 'confirmed'
                  AND bp.zone = 'bootcamp';
                """
            )
        elif target_group == "newbie":
            cursor = await conn.execute(
                """
                SELECT u.telegram_id
                FROM users u
                LEFT JOIN bookings b ON b.telegram_id = u.telegram_id
                    AND b.status = 'confirmed'
                WHERE u.status = 'active'
                GROUP BY u.telegram_id
                HAVING COUNT(b.id) < 3;
                """
            )
        else:
            cursor = await conn.execute(
                "SELECT telegram_id FROM users WHERE status='active';"
            )
        return await cursor.fetchall()


# ===========================================================================
# Broadcasts
# ===========================================================================

async def create_broadcast(
    db_path: str,
    admin_id: int,
    post_id: int,
    target_group: str,
    scheduled_at: Optional[str] = None,
    total_recipients: int = 0,
) -> int:
    status = "scheduled" if scheduled_at else "pending"
    async with _db(db_path) as conn:
        cursor = await conn.execute(
            """
            INSERT INTO broadcasts
                (admin_id, post_id, target_group, scheduled_at,
                 total_recipients, status)
            VALUES (?, ?, ?, ?, ?, ?);
            """,
            (admin_id, post_id, target_group, scheduled_at,
             total_recipients, status),
        )
        await conn.commit()
        return cursor.lastrowid


async def get_broadcast(db_path: str, broadcast_id: int) -> Optional[aiosqlite.Row]:
    async with _db(db_path) as conn:
        cursor = await conn.execute(
            """
            SELECT bc.*, p.title as post_title, p.body as post_body,
                   p.image_url, p.button_text, p.button_action, p.type as post_type
            FROM broadcasts bc
            LEFT JOIN posts p ON bc.post_id = p.id
            WHERE bc.id = ?;
            """,
            (broadcast_id,),
        )
        return await cursor.fetchone()


async def list_broadcasts(db_path: str, limit: int = 10) -> list:
    async with _db(db_path) as conn:
        cursor = await conn.execute(
            """
            SELECT bc.*, p.title as post_title
            FROM broadcasts bc
            LEFT JOIN posts p ON bc.post_id = p.id
            ORDER BY bc.created_at DESC LIMIT ?;
            """,
            (limit,),
        )
        return await cursor.fetchall()


async def get_pending_scheduled_broadcasts(db_path: str) -> list:
    """Рассылки со статусом 'scheduled', время которых уже настало."""
    async with _db(db_path) as conn:
        cursor = await conn.execute(
            """
            SELECT * FROM broadcasts
            WHERE status = 'scheduled'
              AND scheduled_at <= CURRENT_TIMESTAMP;
            """
        )
        return await cursor.fetchall()


async def set_broadcast_status(
    db_path: str,
    broadcast_id: int,
    status: str,
    sent_at: Optional[str] = None,
) -> None:
    async with _db(db_path) as conn:
        await conn.execute(
            "UPDATE broadcasts SET status=?, sent_at=COALESCE(?, sent_at) WHERE id=?;",
            (status, sent_at, broadcast_id),
        )
        await conn.commit()


async def update_broadcast_progress(
    db_path: str,
    broadcast_id: int,
    sent_count: int,
    failed_count: int,
) -> None:
    async with _db(db_path) as conn:
        await conn.execute(
            """
            UPDATE broadcasts
            SET sent_count=?, failed_count=?
            WHERE id=?;
            """,
            (sent_count, failed_count, broadcast_id),
        )
        await conn.commit()


async def set_broadcast_total(
    db_path: str, broadcast_id: int, total: int
) -> None:
    async with _db(db_path) as conn:
        await conn.execute(
            "UPDATE broadcasts SET total_recipients=? WHERE id=?;",
            (total, broadcast_id),
        )
        await conn.commit()


async def add_broadcast_recipient(
    db_path: str,
    broadcast_id: int,
    telegram_id: int,
    status: str,
) -> None:
    async with _db(db_path) as conn:
        await conn.execute(
            """
            INSERT OR REPLACE INTO broadcast_recipients
                (broadcast_id, telegram_id, status)
            VALUES (?, ?, ?);
            """,
            (broadcast_id, telegram_id, status),
        )
        await conn.commit()

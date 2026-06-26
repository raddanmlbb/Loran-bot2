"""
db/migrations.py — система миграций базы данных LORAN.CYBER.
"""

import logging
import os
import sqlite3
from typing import Dict, List

from db.models import MIGRATION_V1, MIGRATION_V2, MIGRATION_V3, MIGRATION_V4

logger = logging.getLogger(__name__)

MIGRATIONS: Dict[int, List[str]] = {
    1: MIGRATION_V1,
    2: MIGRATION_V2,
    3: MIGRATION_V3,
    4: MIGRATION_V4,
}

LATEST_VERSION: int = max(MIGRATIONS.keys())


def _get_current_version(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute("SELECT MAX(version) FROM schema_version;").fetchone()
        return row[0] if row[0] is not None else 0
    except sqlite3.OperationalError:
        return 0


def _ensure_data_dir(db_path: str) -> None:
    directory = os.path.dirname(db_path)
    if directory:
        os.makedirs(directory, exist_ok=True)


def run_migrations(db_path: str) -> None:
    """
    Применяет все необходимые миграции к базе данных.
    Вызывается синхронно при старте бота.
    """
    _ensure_data_dir(db_path)

    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")

    try:
        current_version = _get_current_version(conn)
        logger.info(
            "Версия схемы БД: %d (последняя: %d)", current_version, LATEST_VERSION
        )

        if current_version >= LATEST_VERSION:
            logger.info("База данных актуальна, миграции не требуются.")
            return

        for version in range(current_version + 1, LATEST_VERSION + 1):
            statements = MIGRATIONS.get(version)
            if not statements:
                raise ValueError(f"Миграция версии {version} не определена.")

            logger.info("Применяю миграцию версии %d...", version)
            conn.execute("BEGIN;")
            try:
                for sql in statements:
                    conn.execute(sql)
                conn.execute(
                    "INSERT INTO schema_version (version) VALUES (?);",
                    (version,),
                )
                conn.execute("COMMIT;")
                logger.info("Миграция %d успешно применена.", version)
            except sqlite3.Error as exc:
                conn.execute("ROLLBACK;")
                logger.error("Ошибка при миграции %d: %s. Откат.", version, exc)
                raise
    finally:
        conn.close()

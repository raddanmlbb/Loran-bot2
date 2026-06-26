"""
db/migrations.py — система миграций базы данных LORAN.CYBER.

Логика:
  1. При каждом старте бота вызывается run_migrations(db_path).
  2. Проверяется наличие таблицы schema_version.
  3. Определяется текущая версия схемы (0, если таблица пуста или отсутствует).
  4. Применяются только те миграции, чей номер выше текущей версии.
  5. После успешного применения версия фиксируется в schema_version.

Добавление новой миграции:
  - Добавить ключ с номером версии в словарь MIGRATIONS.
  - Написать список SQL-выражений в db/models.py.
  - Не изменять уже применённые версии.
"""

import logging
import os
import sqlite3
from typing import Dict, List

from db.models import MIGRATION_V1, MIGRATION_V2

logger = logging.getLogger(__name__)

MIGRATIONS: Dict[int, List[str]] = {
    1: MIGRATION_V1,
    2: MIGRATION_V2,
}

LATEST_VERSION: int = max(MIGRATIONS.keys())


def _get_current_version(conn: sqlite3.Connection) -> int:
    """
    Возвращает текущую версию схемы из таблицы schema_version.

    Args:
        conn: активное соединение с SQLite.

    Returns:
        int: номер последней применённой миграции, 0 если БД новая.
    """
    try:
        row = conn.execute("SELECT MAX(version) FROM schema_version;").fetchone()
        return row[0] if row[0] is not None else 0
    except sqlite3.OperationalError:
        return 0


def _ensure_data_dir(db_path: str) -> None:
    """
    Создаёт директорию для файла БД, если она не существует.

    Args:
        db_path: путь к файлу SQLite.
    """
    directory = os.path.dirname(db_path)
    if directory:
        os.makedirs(directory, exist_ok=True)


def run_migrations(db_path: str) -> None:
    """
    Применяет все необходимые миграции к базе данных.

    Функция вызывается синхронно при старте бота, до инициализации
    Telegram Application.

    Args:
        db_path: путь к файлу SQLite.

    Raises:
        sqlite3.Error: при ошибке выполнения SQL (откат транзакции).
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

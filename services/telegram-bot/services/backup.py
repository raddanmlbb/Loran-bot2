"""
services/backup.py — резервное копирование БД LORAN.CYBER. Этап 7.

Логика:
  1. Создаёт копию loran.db в папке backups/ с датой в названии.
  2. Отправляет файл главному администратору в Telegram.
  3. Хранит последние 7 копий, удаляет старые.
"""

import logging
import os
import shutil
from datetime import date
from pathlib import Path
from typing import Optional

from telegram import Bot
from telegram.error import TelegramError

logger = logging.getLogger(__name__)

BACKUP_KEEP_COUNT: int = 7


def _backup_dir(db_path: str) -> Path:
    db_file = Path(db_path)
    return db_file.parent / "backups"


def _backup_filename(db_path: str) -> Path:
    today = date.today().isoformat()
    db_stem = Path(db_path).stem          # "loran"
    return _backup_dir(db_path) / f"{db_stem}_{today}.db"


def create_backup(db_path: str) -> Optional[Path]:
    """
    Создаёт копию файла БД в папке backups/.

    Returns:
        Path к созданному файлу, или None при ошибке.
    """
    src = Path(db_path)
    if not src.exists():
        logger.error("Файл БД не найден: %s", db_path)
        return None

    backup_dir = _backup_dir(db_path)
    backup_dir.mkdir(parents=True, exist_ok=True)

    dest = _backup_filename(db_path)
    try:
        shutil.copy2(src, dest)
        logger.info("Резервная копия создана: %s", dest)
        return dest
    except OSError as exc:
        logger.error("Ошибка создания резервной копии: %s", exc)
        return None


def cleanup_old_backups(db_path: str) -> int:
    """
    Удаляет старые резервные копии, оставляя BACKUP_KEEP_COUNT последних.

    Returns:
        Количество удалённых файлов.
    """
    backup_dir = _backup_dir(db_path)
    if not backup_dir.exists():
        return 0

    stem = Path(db_path).stem
    backups = sorted(
        backup_dir.glob(f"{stem}_*.db"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    deleted = 0
    for old_file in backups[BACKUP_KEEP_COUNT:]:
        try:
            old_file.unlink()
            logger.info("Удалена старая копия: %s", old_file)
            deleted += 1
        except OSError as exc:
            logger.warning("Не удалось удалить %s: %s", old_file, exc)

    return deleted


async def find_chief_admin(db_path: str) -> Optional[int]:
    """Возвращает telegram_id первого главного администратора."""
    from db.queries_posts import _db
    async with _db(db_path) as conn:
        cursor = await conn.execute(
            "SELECT telegram_id FROM users WHERE is_admin=1 AND admin_level='chief' LIMIT 1;"
        )
        row = await cursor.fetchone()
        return row["telegram_id"] if row else None


async def run_backup(bot: Bot, db_path: str) -> None:
    """
    Полный цикл резервного копирования:
      1. Создаёт копию.
      2. Отправляет главному администратору.
      3. Удаляет старые копии.
    """
    today = date.today().isoformat()

    # 1. Создаём копию
    backup_path = create_backup(db_path)
    if backup_path is None:
        logger.error("Резервное копирование не выполнено — не удалось создать файл.")
        return

    # 2. Отправляем главному администратору
    chief_tid = await find_chief_admin(db_path)
    if chief_tid is None:
        logger.warning("Главный администратор не найден — резервная копия не отправлена.")
    else:
        try:
            with open(backup_path, "rb") as f:
                await bot.send_document(
                    chat_id=chief_tid,
                    document=f,
                    filename=backup_path.name,
                    caption=(
                        f"💾 <b>Резервная копия БД</b>\n"
                        f"Дата: {today}\n"
                        f"Файл: {backup_path.name}"
                    ),
                    parse_mode="HTML",
                )
            logger.info("Резервная копия отправлена главному администратору tid=%d", chief_tid)
        except TelegramError as exc:
            logger.error("Не удалось отправить резервную копию: %s", exc)

    # 3. Чистим старые копии
    deleted = cleanup_old_backups(db_path)
    if deleted:
        logger.info("Удалено старых резервных копий: %d", deleted)

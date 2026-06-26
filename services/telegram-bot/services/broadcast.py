"""
services/broadcast.py — ядро сервиса рассылки LORAN.CYBER. Этап 6.

Логика:
  1. Определяем получателей по target_group.
  2. Отправляем сообщение каждому с задержкой (rate limiting: ~25 msg/s).
  3. Пропускаем заблокировавших бота (TelegramError).
  4. Обновляем счётчики прогресса в БД каждые 10 сообщений.
  5. По завершении ставим статус broadcast = 'done'.
"""

import asyncio
import logging
from datetime import datetime, timezone
from html import escape
from typing import Optional

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import Forbidden, TelegramError

from db.queries_posts import (
    add_broadcast_recipient,
    get_broadcast,
    get_recipients,
    set_broadcast_status,
    set_broadcast_total,
    update_broadcast_progress,
)

logger = logging.getLogger(__name__)

# Задержка между сообщениями (сек). ~25 msg/s — лимит Telegram Bot API.
SEND_DELAY: float = 0.05
# Задержка в блоке при Flood Wait
FLOOD_WAIT_EXTRA: float = 1.0
# Обновлять прогресс в БД каждые N сообщений
PROGRESS_BATCH: int = 10


def _build_keyboard(
    button_text: Optional[str],
    button_action: Optional[str],
    webapp_base_url: str,
) -> Optional[InlineKeyboardMarkup]:
    if not button_text or not button_action:
        return None

    if button_action == "booking":
        url = f"{webapp_base_url.rstrip('/')}?screen=booking"
        return InlineKeyboardMarkup([[InlineKeyboardButton(button_text, url=url)]])
    elif button_action == "price":
        url = f"{webapp_base_url.rstrip('/')}?screen=price"
        return InlineKeyboardMarkup([[InlineKeyboardButton(button_text, url=url)]])
    elif button_action.startswith("http"):
        return InlineKeyboardMarkup([[InlineKeyboardButton(button_text, url=button_action)]])
    return None


def _format_message(
    post_type: str,
    title: str,
    body: str,
    promo_code: Optional[str] = None,
    expires_at: Optional[str] = None,
) -> str:
    type_prefix = "📣 Акция" if post_type == "promo" else "📰 Новости"
    lines = [
        f"<b>{type_prefix}: {escape(title)}</b>",
        "",
        escape(body),
    ]
    if promo_code:
        lines += ["", f"🎟 Промокод: <code>{escape(promo_code)}</code>"]
    if expires_at:
        lines += [f"⏳ Действует до: {expires_at}"]
    return "\n".join(lines)


async def run_broadcast(
    db_path: str,
    bot: Bot,
    broadcast_id: int,
    webapp_base_url: str,
    progress_callback=None,
) -> None:
    """
    Выполняет рассылку broadcast_id.

    progress_callback(sent, failed, total) вызывается при обновлении прогресса —
    используется для редактирования сообщения в Telegram.
    """
    broadcast = await get_broadcast(db_path, broadcast_id)
    if broadcast is None:
        logger.error("Broadcast #%d не найден.", broadcast_id)
        return

    if broadcast["status"] not in ("pending", "scheduled"):
        logger.warning(
            "Broadcast #%d уже в статусе %s, пропуск.",
            broadcast_id, broadcast["status"]
        )
        return

    # Определяем получателей
    recipients = await get_recipients(db_path, broadcast["target_group"])
    total = len(recipients)
    await set_broadcast_total(db_path, broadcast_id, total)
    await set_broadcast_status(
        db_path, broadcast_id, "sending",
        sent_at=datetime.now(timezone.utc).isoformat(),
    )

    logger.info(
        "Broadcast #%d: начало рассылки. target_group=%s, recipients=%d",
        broadcast_id, broadcast["target_group"], total,
    )

    # Формируем контент
    message_text = _format_message(
        post_type=broadcast.get("post_type") or "news",
        title=broadcast.get("post_title") or "",
        body=broadcast.get("post_body") or "",
        promo_code=None,
        expires_at=None,
    )
    keyboard = _build_keyboard(
        broadcast.get("button_text"),
        broadcast.get("button_action"),
        webapp_base_url,
    )
    image_url: Optional[str] = broadcast.get("image_url")

    sent_count = 0
    failed_count = 0

    for i, row in enumerate(recipients):
        tid: int = row["telegram_id"]
        try:
            if image_url:
                await bot.send_photo(
                    chat_id=tid,
                    photo=image_url,
                    caption=message_text,
                    parse_mode="HTML",
                    reply_markup=keyboard,
                )
            else:
                await bot.send_message(
                    chat_id=tid,
                    text=message_text,
                    parse_mode="HTML",
                    reply_markup=keyboard,
                )
            sent_count += 1
            await add_broadcast_recipient(db_path, broadcast_id, tid, "sent")

        except Forbidden:
            # Пользователь заблокировал бота
            failed_count += 1
            await add_broadcast_recipient(db_path, broadcast_id, tid, "blocked")
            logger.debug("Broadcast #%d: telegram_id=%d заблокировал бота.", broadcast_id, tid)

        except TelegramError as exc:
            failed_count += 1
            await add_broadcast_recipient(db_path, broadcast_id, tid, "failed")
            logger.warning("Broadcast #%d: ошибка для tid=%d: %s", broadcast_id, tid, exc)
            # При Flood Wait — подождать подольше
            if "Flood" in str(exc) or "Too Many" in str(exc):
                await asyncio.sleep(FLOOD_WAIT_EXTRA)

        except Exception as exc:
            failed_count += 1
            await add_broadcast_recipient(db_path, broadcast_id, tid, "failed")
            logger.exception("Broadcast #%d: неожиданная ошибка tid=%d: %s", broadcast_id, tid, exc)

        # Задержка между отправками
        await asyncio.sleep(SEND_DELAY)

        # Обновляем прогресс периодически
        if (i + 1) % PROGRESS_BATCH == 0 or (i + 1) == total:
            await update_broadcast_progress(db_path, broadcast_id, sent_count, failed_count)
            if progress_callback:
                try:
                    await progress_callback(sent_count, failed_count, total)
                except Exception:
                    pass

    await set_broadcast_status(db_path, broadcast_id, "done")
    logger.info(
        "Broadcast #%d завершён: отправлено=%d, ошибок=%d, всего=%d",
        broadcast_id, sent_count, failed_count, total,
    )

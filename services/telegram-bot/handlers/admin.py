"""
handlers/admin.py — Этап 5: Telegram-интерфейс администратора.

Команда /admin запускает ConversationHandler для авторизации.
После входа показывает меню с разделами.

Безопасность:
  - Пароли хешируются bcrypt.
  - 3 неудачные попытки → блокировка на 15 минут.
  - Все действия записываются в admin_logs.
  - Разграничение уровней: "chief" (главный) и "regular" (обычный).
"""

import logging
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Optional

import bcrypt
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from db.queries import (
    ban_user,
    cancel_booking,
    get_admin_by_login,
    get_admin_logs,
    get_all_bookings,
    get_all_users,
    get_bookings_count_by_day,
    get_club_contacts,
    get_pending_verification_codes,
    get_todays_stats,
    get_top_clients,
    get_top_pcs,
    get_user,
    increment_admin_login_attempts,
    lock_admin_login,
    log_admin_action,
    reject_verification_code,
    reset_admin_login_attempts,
    search_users,
    set_admin_credentials,
    unban_user,
    update_club_contacts,
)

logger = logging.getLogger(__name__)

# Состояния ConversationHandler
(
    AWAIT_LOGIN,
    AWAIT_PASSWORD,
    MAIN_MENU,
    AWAIT_BAN_REASON,
    AWAIT_CANCEL_REASON,
    AWAIT_CONTACT_FIELD,
    AWAIT_CONTACT_VALUE,
    SETUP_ADMIN_LOGIN,
    SETUP_ADMIN_PASSWORD,
    # Этап 6: создание новостей/акций
    POST_TITLE,
    POST_BODY,
    POST_IMAGE,
    POST_BTN_TEXT,
    POST_BTN_ACTION,
    POST_PROMO,
    POST_DATES,
    # Этап 6: рассылка
    BROADCAST_SCHEDULE,
) = range(17)

# Максимум попыток входа перед блокировкой
MAX_LOGIN_ATTEMPTS: int = 3
LOCKOUT_MINUTES: int = 15


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _check_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def _main_menu_keyboard(is_chief: bool = False) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("📊 Дашборд", callback_data="adm_dashboard")],
        [InlineKeyboardButton("📅 Брони", callback_data="adm_bookings"),
         InlineKeyboardButton("👥 Клиенты", callback_data="adm_clients")],
        [InlineKeyboardButton("📰 Новости", callback_data="adm_news"),
         InlineKeyboardButton("📣 Рассылка", callback_data="adm_broadcast")],
        [InlineKeyboardButton("🔑 Senet-запросы", callback_data="adm_senet_requests"),
         InlineKeyboardButton("📈 Статистика", callback_data="adm_stats")],
        [InlineKeyboardButton("📞 Контакты клуба", callback_data="adm_contacts")],
    ]
    if is_chief:
        buttons.append([InlineKeyboardButton("⚙️ Управление админами", callback_data="adm_manage_admins")])
    buttons.append([InlineKeyboardButton("🚪 Выйти", callback_data="adm_logout")])
    return InlineKeyboardMarkup(buttons)


def _store_admin_session(context: ContextTypes.DEFAULT_TYPE, telegram_id: int, admin_level: str) -> None:
    context.user_data["admin_authed"] = True
    context.user_data["admin_telegram_id"] = telegram_id
    context.user_data["admin_level"] = admin_level


def _is_authed(context: ContextTypes.DEFAULT_TYPE) -> bool:
    return bool(context.user_data.get("admin_authed"))


def _is_chief(context: ContextTypes.DEFAULT_TYPE) -> bool:
    return context.user_data.get("admin_level") == "chief"


# ---------------------------------------------------------------------------
# /admin — точка входа
# ---------------------------------------------------------------------------

async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.effective_user is None or update.message is None:
        return ConversationHandler.END

    telegram_id = update.effective_user.id
    db_path: str = context.bot_data["db_path"]

    # Уже авторизован?
    if _is_authed(context):
        user = await get_user(db_path, telegram_id)
        level = context.user_data.get("admin_level", "regular")
        is_chief = level == "chief"
        await update.message.reply_text(
            f"✅ Добро пожаловать в панель администратора, {escape(user['login'] if user else 'Админ')}!",
            reply_markup=_main_menu_keyboard(is_chief),
        )
        return MAIN_MENU

    # Проверяем, есть ли у пользователя is_admin
    user = await get_user(db_path, telegram_id)
    if user is None or not user["is_admin"]:
        await update.message.reply_text("❌ Доступ запрещён.")
        return ConversationHandler.END

    # Если у пользователя ещё нет admin_login — запускаем первичную настройку
    if not user["admin_login"]:
        context.user_data["setup_telegram_id"] = telegram_id
        await update.message.reply_text(
            "🔧 Первый вход. Придумайте логин для admin-панели (только латиница, 4–20 символов):"
        )
        return SETUP_ADMIN_LOGIN

    # Стандартный вход
    await update.message.reply_text("🔐 Введите логин администратора:")
    return AWAIT_LOGIN


# ---------------------------------------------------------------------------
# Первичная настройка admin-логина и пароля
# ---------------------------------------------------------------------------

async def setup_admin_login(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None:
        return SETUP_ADMIN_LOGIN

    login = update.message.text.strip()
    if not (4 <= len(login) <= 20) or not login.isalnum():
        await update.message.reply_text("❌ Логин должен содержать 4–20 символов, только буквы и цифры.")
        return SETUP_ADMIN_LOGIN

    context.user_data["setup_admin_login"] = login
    await update.message.reply_text("🔑 Придумайте пароль (минимум 6 символов):")
    return SETUP_ADMIN_PASSWORD


async def setup_admin_password(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None:
        return SETUP_ADMIN_PASSWORD

    password = update.message.text.strip()
    if len(password) < 6:
        await update.message.reply_text("❌ Пароль должен содержать минимум 6 символов.")
        return SETUP_ADMIN_PASSWORD

    telegram_id = context.user_data.get("setup_telegram_id")
    if not telegram_id:
        await update.message.reply_text("❌ Ошибка сессии. Попробуйте /admin снова.")
        return ConversationHandler.END

    admin_login = context.user_data.get("setup_admin_login", "")
    db_path: str = context.bot_data["db_path"]

    # Если это первый администратор — делаем его chief
    user = await get_user(db_path, telegram_id)
    existing_admins_with_login = await _count_admins_with_login(db_path)
    admin_level = "chief" if existing_admins_with_login == 0 else "regular"

    password_hash = _hash_password(password)
    await set_admin_credentials(db_path, telegram_id, admin_login, password_hash, admin_level)

    _store_admin_session(context, telegram_id, admin_level)
    await log_admin_action(db_path, telegram_id, "admin_setup", details=f"admin_level={admin_level}")

    level_label = "главный администратор" if admin_level == "chief" else "администратор"
    await update.message.reply_text(
        f"✅ Настройка завершена! Вы — {level_label}.\n"
        f"Логин: {admin_login}\n\n"
        "Добро пожаловать в панель управления!",
        reply_markup=_main_menu_keyboard(admin_level == "chief"),
    )
    return MAIN_MENU


async def _count_admins_with_login(db_path: str) -> int:
    from db.queries import _db
    async with _db(db_path) as conn:
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM users WHERE is_admin=1 AND admin_login IS NOT NULL;"
        )
        row = await cursor.fetchone()
        return row[0] if row else 0


# ---------------------------------------------------------------------------
# Авторизация по логину/паролю
# ---------------------------------------------------------------------------

async def await_login(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None:
        return AWAIT_LOGIN

    admin_login = update.message.text.strip()
    context.user_data["entered_admin_login"] = admin_login
    await update.message.reply_text("🔑 Введите пароль:")
    return AWAIT_PASSWORD


async def await_password(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None:
        return AWAIT_PASSWORD

    db_path: str = context.bot_data["db_path"]
    telegram_id = update.effective_user.id
    admin_login = context.user_data.get("entered_admin_login", "")
    password = update.message.text.strip()

    user_row = await get_admin_by_login(db_path, admin_login)
    if user_row is None:
        await update.message.reply_text("❌ Неверный логин или пароль. Попробуйте снова.\n\nВведите логин:")
        return AWAIT_LOGIN

    # Проверяем блокировку
    locked_until_raw = user_row["admin_locked_until"]
    if locked_until_raw:
        try:
            locked_until = datetime.fromisoformat(locked_until_raw).replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) < locked_until:
                minutes_left = int((locked_until - datetime.now(timezone.utc)).total_seconds() / 60) + 1
                await update.message.reply_text(
                    f"🔒 Слишком много попыток. Подождите {minutes_left} мин."
                )
                return ConversationHandler.END
        except ValueError:
            pass

    tid = user_row["telegram_id"]
    if not _check_password(password, user_row["admin_password_hash"] or ""):
        attempts = await increment_admin_login_attempts(db_path, tid)
        remaining = MAX_LOGIN_ATTEMPTS - attempts
        if remaining <= 0:
            locked_until = datetime.now(timezone.utc) + timedelta(minutes=LOCKOUT_MINUTES)
            await lock_admin_login(db_path, tid, locked_until)
            await update.message.reply_text(
                f"🔒 Превышен лимит попыток. Доступ заблокирован на {LOCKOUT_MINUTES} минут."
            )
            return ConversationHandler.END
        await update.message.reply_text(
            f"❌ Неверный пароль. Осталось попыток: {remaining}\n\nВведите логин:"
        )
        return AWAIT_LOGIN

    # Успешный вход
    await reset_admin_login_attempts(db_path, tid)
    admin_level = user_row["admin_level"] or "regular"
    _store_admin_session(context, tid, admin_level)
    await log_admin_action(db_path, tid, "admin_login")

    level_label = "Главный администратор" if admin_level == "chief" else "Администратор"
    await update.message.reply_text(
        f"✅ {level_label} {escape(user_row['login'] or admin_login)}, добро пожаловать!",
        reply_markup=_main_menu_keyboard(admin_level == "chief"),
    )
    return MAIN_MENU


# ---------------------------------------------------------------------------
# Главное меню — callback_query
# ---------------------------------------------------------------------------

async def main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query is None:
        return MAIN_MENU
    await query.answer()

    if not _is_authed(context):
        await query.edit_message_text("❌ Сессия истекла. Введите /admin снова.")
        return ConversationHandler.END

    data = query.data
    db_path: str = context.bot_data["db_path"]
    tid = context.user_data.get("admin_telegram_id")

    if data == "adm_dashboard":
        await _show_dashboard(query, db_path)
    elif data == "adm_bookings":
        await _show_bookings(query, db_path)
    elif data == "adm_clients":
        await _show_clients(query, db_path)
    elif data == "adm_senet_requests":
        await _show_senet_requests(query, db_path)
    elif data == "adm_stats":
        await _show_stats(query, db_path)
    elif data == "adm_contacts":
        await _show_contacts_menu(query, db_path)
    elif data == "adm_manage_admins":
        if _is_chief(context):
            await _show_manage_admins(query, db_path)
        else:
            await query.edit_message_text("❌ Только главный администратор.")
    # ---- Этап 6: Новости ----
    elif data == "adm_news":
        await _show_news_menu(query, db_path)
    elif data in ("adm_create_news", "adm_create_promo"):
        post_type = "news" if data == "adm_create_news" else "promo"
        context.user_data["new_post"] = {"type": post_type}
        await query.edit_message_text("📝 Введите заголовок:")
        return POST_TITLE
    elif data.startswith("adm_publish_post_"):
        post_id = int(data.split("adm_publish_post_")[1])
        from db.queries_posts import publish_post
        await publish_post(db_path, post_id)
        await log_admin_action(db_path, tid, "post_publish", "post", str(post_id))
        await query.edit_message_text(f"✅ Новость #{post_id} опубликована.")
    elif data.startswith("adm_delete_post_"):
        post_id = int(data.split("adm_delete_post_")[1])
        from db.queries_posts import delete_post
        await delete_post(db_path, post_id)
        await log_admin_action(db_path, tid, "post_delete", "post", str(post_id))
        await query.edit_message_text("🗑 Запись удалена.")
    elif data == "adm_news_drafts":
        await _show_posts_list(query, db_path, status="draft", title="Черновики")
    elif data == "adm_news_published":
        await _show_posts_list(query, db_path, status="published", title="Опубликованные")
    # ---- Этап 6: Рассылка ----
    elif data == "adm_broadcast":
        await _show_broadcast_menu(query, db_path)
    elif data.startswith("adm_bcast_post_"):
        post_id = int(data.split("adm_bcast_post_")[1])
        context.user_data["broadcast_post_id"] = post_id
        await _show_broadcast_group_select(query)
    elif data.startswith("adm_bcast_group_"):
        group = data.split("adm_bcast_group_")[1]
        context.user_data["broadcast_group"] = group
        await _show_broadcast_timing(query)
    elif data == "adm_bcast_now":
        await _start_broadcast_now(query, context, db_path)
        return MAIN_MENU
    elif data == "adm_bcast_schedule":
        await query.edit_message_text(
            "Введите дату и время по Астане (UTC+5):\n"
            "Формат: ДД.ММ.ГГГГ ЧЧ:ММ\n"
            "Например: 27.06.2026 18:00"
        )
        return BROADCAST_SCHEDULE
    elif data == "adm_broadcast_history":
        await _show_broadcast_history(query, db_path)
    # ---- Этап 6: Сохранение поста ----
    elif data == "adm_post_save_draft":
        await _handle_save_post(query, context, db_path, "draft")
    elif data == "adm_post_save_pub":
        await _handle_save_post(query, context, db_path, "published")
    # ---- Общие ----
    elif data == "adm_logout":
        context.user_data.clear()
        await log_admin_action(db_path, tid, "admin_logout")
        await query.edit_message_text("👋 Вы вышли из панели администратора.")
        return ConversationHandler.END
    elif data == "adm_back":
        is_chief = _is_chief(context)
        await query.edit_message_text(
            "📋 Меню администратора:", reply_markup=_main_menu_keyboard(is_chief)
        )
    elif data.startswith("adm_ban_"):
        uid = int(data.split("_")[-1])
        context.user_data["ban_target"] = uid
        await query.edit_message_text(f"Введите причину бана для пользователя {uid}:")
        return AWAIT_BAN_REASON
    elif data.startswith("adm_unban_"):
        uid = int(data.split("_")[-1])
        await unban_user(db_path, uid)
        await log_admin_action(db_path, tid, "user_unban", "user", str(uid))
        await query.edit_message_text(f"✅ Пользователь {uid} разбанен.")
    elif data.startswith("adm_cancel_booking_"):
        booking_id = int(data.split("_")[-1])
        context.user_data["cancel_booking_id"] = booking_id
        await query.edit_message_text("Введите причину отмены брони (или '-' чтобы пропустить):")
        return AWAIT_CANCEL_REASON
    elif data.startswith("adm_approve_senet_"):
        code = data.split("adm_approve_senet_")[1]
        from db.queries import mark_verification_code_used
        await mark_verification_code_used(db_path, code)
        await log_admin_action(db_path, tid, "senet_code_approved", "code", code)
        await query.edit_message_text(f"✅ Код {code} подтверждён.")
    elif data.startswith("adm_reject_senet_"):
        code = data.split("adm_reject_senet_")[1]
        await reject_verification_code(db_path, code)
        await log_admin_action(db_path, tid, "senet_code_rejected", "code", code)
        await query.edit_message_text(f"❌ Код {code} отклонён.")
    elif data.startswith("adm_contact_edit_"):
        field = data.split("adm_contact_edit_")[1]
        context.user_data["contact_field"] = field
        field_labels = {
            "phone": "номер телефона", "whatsapp": "WhatsApp",
            "telegram": "Telegram-канал", "instagram": "Instagram"
        }
        await query.edit_message_text(
            f"Введите новое значение для «{field_labels.get(field, field)}»:"
        )
        return AWAIT_CONTACT_VALUE

    return MAIN_MENU


# ---------------------------------------------------------------------------
# Ввод причины бана
# ---------------------------------------------------------------------------

async def await_ban_reason(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None:
        return AWAIT_BAN_REASON

    db_path: str = context.bot_data["db_path"]
    tid = context.user_data.get("admin_telegram_id")
    target_uid = context.user_data.get("ban_target")
    reason = update.message.text.strip() or "Нарушение правил клуба"

    if target_uid:
        await ban_user(db_path, target_uid, reason, tid)
        await log_admin_action(db_path, tid, "user_ban", "user", str(target_uid), reason)
        await update.message.reply_text(
            f"✅ Пользователь {target_uid} заблокирован.\nПричина: {reason}",
            reply_markup=_main_menu_keyboard(_is_chief(context)),
        )

    return MAIN_MENU


# ---------------------------------------------------------------------------
# Ввод причины отмены брони
# ---------------------------------------------------------------------------

async def await_cancel_reason(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None:
        return AWAIT_CANCEL_REASON

    db_path: str = context.bot_data["db_path"]
    tid = context.user_data.get("admin_telegram_id")
    booking_id = context.user_data.get("cancel_booking_id")
    reason = update.message.text.strip()
    if reason == "-":
        reason = ""

    if booking_id:
        await cancel_booking(db_path, booking_id, cancelled_by="admin", reason=reason)
        await log_admin_action(db_path, tid, "booking_cancel", "booking", str(booking_id), reason)
        await update.message.reply_text(
            f"✅ Бронь #{booking_id} отменена.",
            reply_markup=_main_menu_keyboard(_is_chief(context)),
        )

    return MAIN_MENU


# ---------------------------------------------------------------------------
# Ввод нового значения контакта
# ---------------------------------------------------------------------------

async def await_contact_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None:
        return AWAIT_CONTACT_VALUE

    db_path: str = context.bot_data["db_path"]
    tid = context.user_data.get("admin_telegram_id")
    field = context.user_data.get("contact_field", "")
    value = update.message.text.strip()

    kwargs = {field: value, "updated_by": tid}
    await update_club_contacts(db_path, **kwargs)
    await log_admin_action(db_path, tid, "contacts_update", "contacts", field, value)

    await update.message.reply_text(
        f"✅ Поле «{field}» обновлено: {value}",
        reply_markup=_main_menu_keyboard(_is_chief(context)),
    )
    return MAIN_MENU


# ---------------------------------------------------------------------------
# Разделы панели
# ---------------------------------------------------------------------------

async def _show_dashboard(query, db_path: str) -> None:
    stats = await get_todays_stats(db_path)
    text = (
        "📊 <b>Дашборд — Сегодня</b>\n\n"
        f"📅 Брони: {stats.get('booking_count', 0)}\n"
        f"💰 Выручка: {stats.get('revenue', 0):,} ₸\n"
        f"👥 Уникальных клиентов: {stats.get('unique_clients', 0)}\n"
        f"🖥 ПК занято сейчас: {stats.get('booked_pcs_now', 0)}/23\n"
    )

    senet_requests = await get_pending_verification_codes(db_path)
    if senet_requests:
        text += f"\n🔔 Запросов на привязку Senet: {len(senet_requests)}"

    buttons = [[InlineKeyboardButton("↩️ Назад", callback_data="adm_back")]]
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons))


async def _show_bookings(query, db_path: str) -> None:
    from datetime import date
    today = date.today().isoformat()
    bookings = await get_all_bookings(db_path, date_filter=today, limit=10)

    if not bookings:
        text = "📅 <b>Брони на сегодня</b>\n\nНет бронирований."
        buttons = [[InlineKeyboardButton("↩️ Назад", callback_data="adm_back")]]
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons))
        return

    lines = ["📅 <b>Брони на сегодня</b>\n"]
    action_buttons = []

    for b in bookings[:8]:
        status_icon = "✅" if b["status"] == "confirmed" else "❌"
        lines.append(
            f"{status_icon} #{b['id']} | {b['login']} | {b['time_from']}–{b['time_to']} "
            f"| ПК: {b['pc_list']} | {b['total_price']:,}₸"
        )
        if b["status"] == "confirmed":
            action_buttons.append([
                InlineKeyboardButton(
                    f"❌ Отменить #{b['id']}",
                    callback_data=f"adm_cancel_booking_{b['id']}"
                )
            ])

    action_buttons.append([InlineKeyboardButton("↩️ Назад", callback_data="adm_back")])
    await query.edit_message_text(
        "\n".join(lines), parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(action_buttons)
    )


async def _show_clients(query, db_path: str) -> None:
    users = await get_all_users(db_path, limit=10)

    if not users:
        text = "👥 <b>Клиенты</b>\n\nНет зарегистрированных пользователей."
        buttons = [[InlineKeyboardButton("↩️ Назад", callback_data="adm_back")]]
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons))
        return

    lines = ["👥 <b>Последние клиенты</b>\n"]
    action_buttons = []

    for u in users[:8]:
        status_icon = "✅" if u["status"] == "active" else "🚫"
        senet = "✓" if u["senet_verified"] else "✗"
        lines.append(
            f"{status_icon} {escape(u['login'])} | {u['phone'] or '—'} | Senet: {senet}"
        )
        if u["status"] == "active":
            action_buttons.append([
                InlineKeyboardButton(
                    f"🚫 Бан: {u['login']}",
                    callback_data=f"adm_ban_{u['telegram_id']}"
                )
            ])
        else:
            action_buttons.append([
                InlineKeyboardButton(
                    f"✅ Разбан: {u['login']}",
                    callback_data=f"adm_unban_{u['telegram_id']}"
                )
            ])

    action_buttons.append([InlineKeyboardButton("↩️ Назад", callback_data="adm_back")])
    await query.edit_message_text(
        "\n".join(lines), parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(action_buttons)
    )


async def _show_senet_requests(query, db_path: str) -> None:
    requests = await get_pending_verification_codes(db_path)

    if not requests:
        text = "🔑 <b>Запросы на привязку Senet</b>\n\nНет активных запросов."
        buttons = [[InlineKeyboardButton("↩️ Назад", callback_data="adm_back")]]
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons))
        return

    lines = ["🔑 <b>Запросы на привязку Senet</b>\n"]
    action_buttons = []

    for r in requests[:5]:
        lines.append(f"Код: <code>{r['code']}</code> | Логин: {escape(r['senet_login'])}")
        action_buttons.append([
            InlineKeyboardButton("✅ Подтвердить", callback_data=f"adm_approve_senet_{r['code']}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"adm_reject_senet_{r['code']}"),
        ])

    action_buttons.append([InlineKeyboardButton("↩️ Назад", callback_data="adm_back")])
    await query.edit_message_text(
        "\n".join(lines), parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(action_buttons)
    )


async def _show_stats(query, db_path: str) -> None:
    by_day = await get_bookings_count_by_day(db_path, days=7)
    top_clients = await get_top_clients(db_path, limit=5)
    top_pcs = await get_top_pcs(db_path, limit=5)

    lines = ["📈 <b>Статистика за 7 дней</b>\n"]

    if by_day:
        lines.append("📅 По дням:")
        for row in by_day:
            lines.append(f"  {row['date']}: {row['count']} брони, {row['revenue']:,}₸")
    else:
        lines.append("Нет данных за 7 дней.")

    if top_clients:
        lines.append("\n🏆 Топ клиентов:")
        for i, c in enumerate(top_clients, 1):
            lines.append(f"  {i}. {escape(c['login'])} — {c['booking_count']} брони")

    if top_pcs:
        lines.append("\n🖥 Топ ПК:")
        for i, p in enumerate(top_pcs, 1):
            lines.append(f"  {i}. ПК {p['pc_id']} ({p['zone']}) — {p['usage_count']} раз")

    buttons = [[InlineKeyboardButton("↩️ Назад", callback_data="adm_back")]]
    await query.edit_message_text(
        "\n".join(lines), parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def _show_contacts_menu(query, db_path: str) -> None:
    contacts = await get_club_contacts(db_path)

    text = "📞 <b>Контакты клуба</b>\n\n"
    if contacts:
        text += f"Телефон: {contacts['phone'] or '—'}\n"
        text += f"WhatsApp: {contacts['whatsapp'] or '—'}\n"
        text += f"Telegram: {contacts['telegram'] or '—'}\n"
        text += f"Instagram: {contacts['instagram'] or '—'}\n"
    else:
        text += "Контакты не заданы.\n"

    buttons = [
        [InlineKeyboardButton("📱 Изменить телефон", callback_data="adm_contact_edit_phone")],
        [InlineKeyboardButton("💬 Изменить WhatsApp", callback_data="adm_contact_edit_whatsapp")],
        [InlineKeyboardButton("✈️ Изменить Telegram", callback_data="adm_contact_edit_telegram")],
        [InlineKeyboardButton("📸 Изменить Instagram", callback_data="adm_contact_edit_instagram")],
        [InlineKeyboardButton("↩️ Назад", callback_data="adm_back")],
    ]
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons))


async def _show_manage_admins(query, db_path: str) -> None:
    from db.queries import get_all_users
    admins = await get_all_users(db_path, limit=50)
    admin_list = [u for u in admins if u["is_admin"]]

    lines = ["⚙️ <b>Администраторы</b>\n"]
    for a in admin_list:
        level = a["admin_level"] or "regular"
        level_icon = "👑" if level == "chief" else "🔑"
        lines.append(f"{level_icon} {escape(a['login'])} | {a['admin_login'] or '—'} | {level}")

    lines.append("\nДля назначения нового администратора установите is_admin=1 в БД.")

    buttons = [[InlineKeyboardButton("↩️ Назад", callback_data="adm_back")]]
    await query.edit_message_text(
        "\n".join(lines), parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


# ===========================================================================
# Этап 6: Новости — создание (ConversationHandler states)
# ===========================================================================

async def post_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None:
        return POST_TITLE
    title = update.message.text.strip()
    if len(title) < 3 or len(title) > 200:
        await update.message.reply_text("❌ Заголовок: от 3 до 200 символов. Введите ещё раз:")
        return POST_TITLE
    context.user_data.setdefault("new_post", {})["title"] = title
    await update.message.reply_text("📝 Введите текст новости (основной блок):")
    return POST_BODY


async def post_body(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None:
        return POST_BODY
    body = update.message.text.strip()
    if len(body) < 3:
        await update.message.reply_text("❌ Текст слишком короткий. Введите ещё раз:")
        return POST_BODY
    context.user_data["new_post"]["body"] = body
    await update.message.reply_text(
        "🖼 Вставьте ссылку на обложку (картинка, прямая ссылка)\n"
        "Или «-» чтобы пропустить:"
    )
    return POST_IMAGE


async def post_image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None:
        return POST_IMAGE
    text = update.message.text.strip()
    context.user_data["new_post"]["image_url"] = None if text == "-" else text
    await update.message.reply_text(
        "🔘 Введите текст кнопки под постом\n"
        "Или «-» чтобы пропустить:"
    )
    return POST_BTN_TEXT


async def post_btn_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None:
        return POST_BTN_TEXT
    text = update.message.text.strip()
    if text == "-":
        context.user_data["new_post"]["button_text"] = None
        context.user_data["new_post"]["button_action"] = None
        return await _after_button(update, context)
    context.user_data["new_post"]["button_text"] = text
    await update.message.reply_text(
        "🔗 Введите действие кнопки:\n"
        "• <code>booking</code> — форма бронирования\n"
        "• <code>price</code> — прайс-лист\n"
        "• <code>https://...</code> — ссылка\n",
        parse_mode="HTML"
    )
    return POST_BTN_ACTION


async def post_btn_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None:
        return POST_BTN_ACTION
    action = update.message.text.strip()
    if action not in ("booking", "price") and not action.startswith("http"):
        await update.message.reply_text(
            "❌ Укажите: booking, price, или https://...\n"
            "Или «-» чтобы убрать кнопку:"
        )
        return POST_BTN_ACTION
    context.user_data["new_post"]["button_action"] = action if action != "-" else None
    return await _after_button(update, context)


async def _after_button(update, context) -> int:
    """Следующий шаг после выбора кнопки — зависит от типа поста."""
    post_type = context.user_data.get("new_post", {}).get("type", "news")
    if post_type == "promo":
        await update.message.reply_text(
            "🎟 Введите промокод (или «-» — без промокода):"
        )
        return POST_PROMO
    else:
        return await _save_post_ask(update, context)


async def post_promo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None:
        return POST_PROMO
    text = update.message.text.strip()
    context.user_data["new_post"]["promo_code"] = None if text == "-" else text
    await update.message.reply_text(
        "📅 Введите срок действия акции:\n"
        "Формат: ГГГГ-ММ-ДД ГГГГ-ММ-ДД (начало конец)\n"
        "Или «-» — без ограничений:"
    )
    return POST_DATES


async def post_dates(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None:
        return POST_DATES
    text = update.message.text.strip()
    if text == "-":
        context.user_data["new_post"]["starts_at"] = None
        context.user_data["new_post"]["expires_at"] = None
    else:
        parts = text.split()
        if len(parts) != 2:
            await update.message.reply_text(
                "❌ Формат: ГГГГ-ММ-ДД ГГГГ-ММ-ДД\nПопробуйте снова:"
            )
            return POST_DATES
        context.user_data["new_post"]["starts_at"] = parts[0]
        context.user_data["new_post"]["expires_at"] = parts[1]
    return await _save_post_ask(update, context)


async def _save_post_ask(update, context) -> int:
    """Показывает превью поста и кнопки «Сохранить как черновик» / «Опубликовать»."""
    p = context.user_data.get("new_post", {})
    lines = [
        f"<b>Готово! Превью:</b>",
        f"Тип: {'Акция' if p.get('type') == 'promo' else 'Новость'}",
        f"Заголовок: {escape(p.get('title', ''))}",
        f"Текст: {escape((p.get('body') or '')[:100])}...",
    ]
    if p.get("image_url"):
        lines.append(f"Обложка: {p['image_url']}")
    if p.get("button_text"):
        lines.append(f"Кнопка: {p['button_text']} → {p.get('button_action')}")
    if p.get("promo_code"):
        lines.append(f"Промокод: {p['promo_code']}")
    if p.get("expires_at"):
        lines.append(f"Срок: {p.get('starts_at')} – {p.get('expires_at')}")

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💾 Черновик", callback_data="adm_post_save_draft"),
            InlineKeyboardButton("🚀 Опубликовать", callback_data="adm_post_save_pub"),
        ],
        [InlineKeyboardButton("❌ Отменить", callback_data="adm_back")],
    ])
    await update.message.reply_text(
        "\n".join(lines), parse_mode="HTML", reply_markup=keyboard
    )
    return MAIN_MENU


# Сохранение поста через callback из MAIN_MENU
async def _handle_save_post(
    query, context, db_path: str, status: str
) -> None:
    from db.queries_posts import create_post
    tid = context.user_data.get("admin_telegram_id")
    p = context.user_data.pop("new_post", {})
    if not p:
        await query.edit_message_text("❌ Данные не найдены. Начните создание заново.")
        return
    post_id = await create_post(
        db_path=db_path,
        admin_id=tid,
        post_type=p.get("type", "news"),
        title=p.get("title", ""),
        body=p.get("body", ""),
        image_url=p.get("image_url"),
        button_text=p.get("button_text"),
        button_action=p.get("button_action"),
        promo_code=p.get("promo_code"),
        starts_at=p.get("starts_at"),
        expires_at=p.get("expires_at"),
        status=status,
    )
    label = "черновик" if status == "draft" else "новость"
    await query.edit_message_text(
        f"✅ Сохранено как {label}. ID: #{post_id}",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("↩️ В меню", callback_data="adm_back")]
        ]),
    )


# ===========================================================================
# Этап 6: Рассылка — scheduling
# ===========================================================================

async def broadcast_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает ввод даты/времени для отложенной рассылки."""
    if update.message is None:
        return BROADCAST_SCHEDULE

    import re
    from datetime import timezone, timedelta

    text = update.message.text.strip()
    # Формат: ДД.ММ.ГГГГ ЧЧ:ММ
    match = re.match(r"^(\d{2})\.(\d{2})\.(\d{4})\s+(\d{2}):(\d{2})$", text)
    if not match:
        await update.message.reply_text(
            "❌ Неверный формат. Используйте ДД.ММ.ГГГГ ЧЧ:ММ\n"
            "Например: 27.06.2026 18:00"
        )
        return BROADCAST_SCHEDULE

    day, month, year, hour, minute = [int(x) for x in match.groups()]
    try:
        # Астана = UTC+5
        from datetime import datetime
        astana_tz = timezone(timedelta(hours=5))
        dt_astana = datetime(year, month, day, hour, minute, tzinfo=astana_tz)
        dt_utc = dt_astana.astimezone(timezone.utc)
    except ValueError:
        await update.message.reply_text("❌ Неверная дата. Попробуйте снова:")
        return BROADCAST_SCHEDULE

    if dt_utc <= datetime.now(timezone.utc):
        await update.message.reply_text("❌ Дата уже прошла. Введите будущую дату:")
        return BROADCAST_SCHEDULE

    db_path: str = context.bot_data["db_path"]
    tid = context.user_data.get("admin_telegram_id")
    post_id: int = context.user_data.get("broadcast_post_id")
    group: str = context.user_data.get("broadcast_group", "all")

    from db.queries_posts import create_broadcast, get_recipients
    recipients = await get_recipients(db_path, group)
    broadcast_id = await create_broadcast(
        db_path=db_path,
        admin_id=tid,
        post_id=post_id,
        target_group=group,
        scheduled_at=dt_utc.isoformat(),
        total_recipients=len(recipients),
    )

    dt_label = dt_astana.strftime("%d.%m.%Y %H:%M")
    group_labels = {"all": "Все", "active": "Активные", "bootcamp": "BOOTCAMP", "newbie": "Новички"}
    await update.message.reply_text(
        f"⏰ Рассылка #{broadcast_id} запланирована на {dt_label} (Астана)\n"
        f"Группа: {group_labels.get(group, group)}, ~{len(recipients)} чел.",
        reply_markup=_main_menu_keyboard(_is_chief(context)),
    )
    return MAIN_MENU


# ===========================================================================
# Этап 6: Show-функции для новостей и рассылки
# ===========================================================================

async def _show_news_menu(query, db_path: str) -> None:
    from db.queries_posts import list_posts
    posts = await list_posts(db_path, limit=5)
    draft_count = sum(1 for p in posts if p["status"] == "draft")
    pub_count = sum(1 for p in posts if p["status"] == "published")

    text = (
        f"📰 <b>Новости и акции</b>\n\n"
        f"Черновиков: {draft_count}\n"
        f"Опубликовано: {pub_count}\n"
    )
    buttons = [
        [InlineKeyboardButton("✏️ Создать новость", callback_data="adm_create_news"),
         InlineKeyboardButton("🎁 Создать акцию", callback_data="adm_create_promo")],
        [InlineKeyboardButton("📋 Черновики", callback_data="adm_news_drafts"),
         InlineKeyboardButton("📢 Опубликованные", callback_data="adm_news_published")],
        [InlineKeyboardButton("↩️ Назад", callback_data="adm_back")],
    ]
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons))


async def _show_posts_list(query, db_path: str, status: str, title: str) -> None:
    from db.queries_posts import list_posts
    posts = await list_posts(db_path, status=status, limit=8)

    type_icon = {"news": "📰", "promo": "🎁"}
    lines = [f"📋 <b>{title}</b>\n"]
    buttons = []

    if not posts:
        lines.append("Нет записей.")
    for p in posts:
        icon = type_icon.get(p["type"], "📄")
        lines.append(f"{icon} #{p['id']} {escape(p['title'][:40])}")
        row = []
        if p["status"] == "draft":
            row.append(InlineKeyboardButton(
                f"🚀 Опубликовать #{p['id']}",
                callback_data=f"adm_publish_post_{p['id']}"
            ))
        row.append(InlineKeyboardButton(
            f"🗑 #{p['id']}",
            callback_data=f"adm_delete_post_{p['id']}"
        ))
        buttons.append(row)

    buttons.append([InlineKeyboardButton("↩️ Назад", callback_data="adm_news")])
    await query.edit_message_text(
        "\n".join(lines), parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def _show_broadcast_menu(query, db_path: str) -> None:
    from db.queries_posts import list_posts, list_broadcasts
    posts = await list_posts(db_path, status="published", limit=5)
    broadcasts = await list_broadcasts(db_path, limit=3)

    text = "📣 <b>Рассылка</b>\n\n"
    if broadcasts:
        text += "Последние рассылки:\n"
        status_icon = {"done": "✅", "sending": "📤", "pending": "⏳", "scheduled": "⏰", "failed": "❌"}
        for b in broadcasts:
            icon = status_icon.get(b["status"], "•")
            total = b["total_recipients"] or 0
            sent = b["sent_count"] or 0
            text += f"{icon} #{b['id']} {escape((b['post_title'] or '')[:30])} ({sent}/{total})\n"
        text += "\n"

    if not posts:
        text += "⚠️ Нет опубликованных новостей.\nСначала создайте новость в разделе 📰."
        buttons = [
            [InlineKeyboardButton("📰 Перейти к новостям", callback_data="adm_news")],
            [InlineKeyboardButton("↩️ Назад", callback_data="adm_back")],
        ]
    else:
        text += "Выберите новость для рассылки:"
        post_buttons = []
        for p in posts:
            icon = "📰" if p["type"] == "news" else "🎁"
            post_buttons.append([InlineKeyboardButton(
                f"{icon} #{p['id']} {p['title'][:35]}",
                callback_data=f"adm_bcast_post_{p['id']}"
            )])
        post_buttons.append([InlineKeyboardButton("📜 История рассылок", callback_data="adm_broadcast_history")])
        post_buttons.append([InlineKeyboardButton("↩️ Назад", callback_data="adm_back")])
        buttons = post_buttons

    await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons))


async def _show_broadcast_group_select(query) -> None:
    text = "👥 <b>Выберите группу получателей:</b>"
    buttons = [
        [InlineKeyboardButton("🌍 Все", callback_data="adm_bcast_group_all")],
        [InlineKeyboardButton("🔥 Активные (30 дней)", callback_data="adm_bcast_group_active")],
        [InlineKeyboardButton("💪 BOOTCAMP", callback_data="adm_bcast_group_bootcamp")],
        [InlineKeyboardButton("🆕 Новички (<3 брони)", callback_data="adm_bcast_group_newbie")],
        [InlineKeyboardButton("↩️ Назад", callback_data="adm_broadcast")],
    ]
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons))


async def _show_broadcast_timing(query) -> None:
    text = "⏰ <b>Когда отправить?</b>"
    buttons = [
        [InlineKeyboardButton("🚀 Сейчас", callback_data="adm_bcast_now")],
        [InlineKeyboardButton("📅 По расписанию", callback_data="adm_bcast_schedule")],
        [InlineKeyboardButton("↩️ Назад", callback_data="adm_broadcast")],
    ]
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons))


async def _start_broadcast_now(query, context, db_path: str) -> None:
    """Создаёт broadcast и запускает рассылку через job_queue."""
    from db.queries_posts import create_broadcast, get_recipients

    tid = context.user_data.get("admin_telegram_id")
    post_id: int = context.user_data.get("broadcast_post_id")
    group: str = context.user_data.get("broadcast_group", "all")

    recipients = await get_recipients(db_path, group)
    total = len(recipients)

    broadcast_id = await create_broadcast(
        db_path=db_path,
        admin_id=tid,
        post_id=post_id,
        target_group=group,
        total_recipients=total,
    )

    group_labels = {"all": "Все", "active": "Активные", "bootcamp": "BOOTCAMP", "newbie": "Новички"}
    progress_msg = await query.edit_message_text(
        f"🚀 Рассылка #{broadcast_id} запущена!\n"
        f"Группа: {group_labels.get(group, group)}, {total} получателей\n"
        f"📤 Прогресс: 0/{total}..."
    )

    # Запускаем рассылку через job_queue (не блокирует event loop)
    app = query.message.get_bot()
    context.application.job_queue.run_once(
        _broadcast_job,
        when=1,
        data={
            "broadcast_id": broadcast_id,
            "chat_id": query.message.chat_id,
            "message_id": progress_msg.message_id,
        },
        name=f"broadcast_{broadcast_id}",
    )


async def _broadcast_job(context) -> None:
    """PTB job: выполняет рассылку в фоне и обновляет прогресс в чате."""
    from services.broadcast import run_broadcast

    data = context.job.data
    broadcast_id: int = data["broadcast_id"]
    chat_id: int = data["chat_id"]
    message_id: int = data["message_id"]
    db_path: str = context.bot_data["db_path"]
    webapp_base_url: str = context.bot_data["webapp_base_url"]

    last_sent = [0]

    async def on_progress(sent: int, failed: int, total: int) -> None:
        # Обновляем сообщение не чаще раза на каждые 10 отправленных
        if sent - last_sent[0] >= 10 or sent == total:
            last_sent[0] = sent
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=(
                        f"📤 Рассылка #{broadcast_id}\n"
                        f"Отправлено: {sent}/{total}\n"
                        f"Ошибок: {failed}"
                    ),
                )
            except Exception:
                pass

    await run_broadcast(
        db_path=db_path,
        bot=context.bot,
        broadcast_id=broadcast_id,
        webapp_base_url=webapp_base_url,
        progress_callback=on_progress,
    )

    # Финальное сообщение о завершении
    from db.queries_posts import get_broadcast
    bc = await get_broadcast(db_path, broadcast_id)
    if bc:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=(
                    f"✅ Рассылка #{broadcast_id} завершена!\n"
                    f"📤 Отправлено: {bc['sent_count']}\n"
                    f"❌ Ошибок: {bc['failed_count']}\n"
                    f"👥 Всего: {bc['total_recipients']}"
                ),
            )
        except Exception:
            pass


async def _show_broadcast_history(query, db_path: str) -> None:
    from db.queries_posts import list_broadcasts
    broadcasts = await list_broadcasts(db_path, limit=8)

    status_icon = {"done": "✅", "sending": "📤", "pending": "⏳", "scheduled": "⏰", "failed": "❌"}
    status_label = {
        "done": "завершена", "sending": "отправляется",
        "pending": "ожидает", "scheduled": "запланирована", "failed": "ошибка",
    }
    lines = ["📜 <b>История рассылок</b>\n"]

    if not broadcasts:
        lines.append("Рассылок не было.")
    for b in broadcasts:
        icon = status_icon.get(b["status"], "•")
        total = b["total_recipients"] or 0
        sent = b["sent_count"] or 0
        failed = b["failed_count"] or 0
        date = (b["created_at"] or "")[:10]
        status = status_label.get(b["status"], b["status"])
        lines.append(
            f"{icon} #{b['id']} {escape((b['post_title'] or 'Без названия')[:25])}\n"
            f"   {date} | {status} | {sent}/{total} (❌{failed})"
        )

    buttons = [[InlineKeyboardButton("↩️ Назад", callback_data="adm_broadcast")]]
    await query.edit_message_text(
        "\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons)
    )


# ---------------------------------------------------------------------------
# Отмена / таймаут ConversationHandler
# ---------------------------------------------------------------------------

async def cancel_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message:
        await update.message.reply_text("❌ Выход из панели администратора.")
    context.user_data.clear()
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# Сборка ConversationHandler
# ---------------------------------------------------------------------------

def build_admin_handler() -> ConversationHandler:
    text_only = filters.TEXT & ~filters.COMMAND
    return ConversationHandler(
        entry_points=[CommandHandler("admin", cmd_admin)],
        states={
            SETUP_ADMIN_LOGIN:    [MessageHandler(text_only, setup_admin_login)],
            SETUP_ADMIN_PASSWORD: [MessageHandler(text_only, setup_admin_password)],
            AWAIT_LOGIN:          [MessageHandler(text_only, await_login)],
            AWAIT_PASSWORD:       [MessageHandler(text_only, await_password)],
            MAIN_MENU: [
                CallbackQueryHandler(main_menu_callback, pattern=r"^adm_"),
            ],
            AWAIT_BAN_REASON:     [MessageHandler(text_only, await_ban_reason)],
            AWAIT_CANCEL_REASON:  [MessageHandler(text_only, await_cancel_reason)],
            AWAIT_CONTACT_VALUE:  [MessageHandler(text_only, await_contact_value)],
            # Этап 6: создание новостей/акций
            POST_TITLE:      [MessageHandler(text_only, post_title)],
            POST_BODY:       [MessageHandler(text_only, post_body)],
            POST_IMAGE:      [MessageHandler(text_only, post_image)],
            POST_BTN_TEXT:   [MessageHandler(text_only, post_btn_text)],
            POST_BTN_ACTION: [MessageHandler(text_only, post_btn_action)],
            POST_PROMO:      [MessageHandler(text_only, post_promo)],
            POST_DATES:      [MessageHandler(text_only, post_dates)],
            # Этап 6: планирование рассылки
            BROADCAST_SCHEDULE: [MessageHandler(text_only, broadcast_schedule)],
        },
        fallbacks=[CommandHandler("cancel", cancel_admin)],
        per_user=True,
        per_chat=True,
        per_message=False,
    )

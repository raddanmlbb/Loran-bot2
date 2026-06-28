"""
main.py — точка входа LORAN.CYBER Bot. Этап 5.

Порядок запуска:
  1. Загрузка конфигурации из переменных окружения.
  2. Синхронный запуск миграций БД.
  3. Синхронизация цен из Senet API.
  4. Сборка PTB Application с job_queue.
  5. Регистрация хендлеров.
  6. run_polling().

Обязательные переменные окружения:
  BOT_TOKEN — токен от @BotFather.

Опциональные:
  DB_PATH, SENET_MODE, SENET_API_URL, SENET_API_KEY,
  WEBAPP_BASE_URL, ⁸INITIAL_ADMIN_IDS
"""

import logging
import sys

from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
)

from config import Config, load_config
from db.migrations import run_migrations
from handlers.admin import build_admin_handler
from handlers.birthday import build_birthday_handler, build_bday_inline_handler
from handlers.commands import cmd_help, cmd_profile, cmd_start
from handlers.messages import (
    handle_address,
    handle_booking,
    handle_contacts,
    handle_news,
    handle_price,
    handle_profile,
    handle_register,
)
from handlers.webapp import handle_webapp_data
from locales.ru import MESSAGES
from senet_api import SenetAPI
from services.pricing import sync_prices_from_senet

# ---------------------------------------------------------------------------
# Логирование
# ---------------------------------------------------------------------------

logging.basicConfig(
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.INFO,
    stream=sys.stdout,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Job: обновление цен из Senet каждый час
# ---------------------------------------------------------------------------


async def _job_sync_prices(context) -> None:
    """Фоновая задача: синхронизация цен из Senet API раз в час."""
    db_path: str = context.bot_data["db_path"]
    senet: SenetAPI = context.bot_data["senet"]
    ok = await sync_prices_from_senet(db_path, senet)
    if ok:
        logger.info("Цены успешно обновлены из Senet (фоновая задача).")
    else:
        logger.warning("Не удалось обновить цены из Senet, используется кэш.")


async def _job_birthday_greetings(context) -> None:
    """Ежедневная задача: поздравления именинников."""
    from services.birthday import run_birthday_greetings

    db_path: str = context.bot_data["db_path"]
    try:
        await run_birthday_greetings(context.bot, db_path)
    except Exception:
        logger.exception("Ошибка в _job_birthday_greetings")


async def _job_backup(context) -> None:
    """Ежедневная задача: резервное копирование БД."""
    from services.backup import run_backup

    db_path: str = context.bot_data["db_path"]
    try:
        await run_backup(context.bot, db_path)
    except Exception:
        logger.exception("Ошибка в _job_backup")


async def _job_scheduled_broadcasts(context) -> None:
    """
    Фоновая задача: каждую минуту проверяет запланированные рассылки
    и запускает те, время которых подошло.
    """
    from db.queries_posts import get_pending_scheduled_broadcasts
    from handlers.admin import _broadcast_job

    db_path: str = context.bot_data["db_path"]

    try:
        due = await get_pending_scheduled_broadcasts(db_path)
        for bc in due:
            bid = bc["id"]
            job_name = f"broadcast_{bid}"
            # Не запускать повторно если уже в очереди
            existing = context.job_queue.get_jobs_by_name(job_name)
            if existing:
                continue
            logger.info("Запускаю запланированную рассылку #%d", bid)
            context.job_queue.run_once(
                _broadcast_job,
                when=1,
                data={"broadcast_id": bid, "chat_id": None, "message_id": None},
                name=job_name,
            )
    except Exception:
        logger.exception("Ошибка в _job_scheduled_broadcasts")


# ---------------------------------------------------------------------------
# Регистрация хендлеров
# ---------------------------------------------------------------------------


def _register_handlers(app: Application) -> None:
    """
    Регистрирует все хендлеры PTB.

    Порядок важен:
      1. ConversationHandler (admin) — первый, чтобы перехватывать всё.
      2. Команды.
      3. WEB_APP_DATA — до текстовых хендлеров.
      4. Текстовые кнопки.
    """
    # Этап 5: ConversationHandler для /admin
    app.add_handler(build_admin_handler())

    # Этап 7: ConversationHandler для /birthday (ввод даты рождения)
    app.add_handler(build_birthday_handler())
    # Inline-кнопки bday_later / bday_never (вне диалога)
    app.add_handler(build_bday_inline_handler())

    # Команды
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("profile", cmd_profile))

    # Данные от Web App
    app.add_handler(
        MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_webapp_data)
    )

    # Кнопки Reply-клавиатуры
    app.add_handler(
        MessageHandler(filters.Text([MESSAGES["btn_booking"]]), handle_booking)
    )
    app.add_handler(MessageHandler(filters.Text([MESSAGES["btn_price"]]), handle_price))
    app.add_handler(MessageHandler(filters.Text([MESSAGES["btn_news"]]), handle_news))
    app.add_handler(
        MessageHandler(filters.Text([MESSAGES["btn_profile"]]), handle_profile)
    )
    app.add_handler(
        MessageHandler(filters.Text([MESSAGES["btn_address"]]), handle_address)
    )
    app.add_handler(
        MessageHandler(filters.Text([MESSAGES["btn_contacts"]]), handle_contacts)
    )
    app.add_handler(
        MessageHandler(filters.Text([MESSAGES["btn_register"]]), handle_register)
    )


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------


def main() -> None:
    """
    Инициализирует и запускает бота.
    """
    # 1. Конфигурация
    try:
        config: Config = load_config()
    except ValueError as exc:
        logger.critical("Ошибка конфигурации: %s", exc)
        sys.exit(1)

    logger.info(
        "Конфигурация загружена. SENET_MODE=%s, DB_PATH=%s",
        config.senet_mode,
        config.db_path,
    )

    # 2. Миграции БД (синхронно, до старта PTB)
    try:
        run_migrations(config.db_path)
    except Exception as exc:
        logger.critical("Не удалось применить миграции БД: %s", exc)
        sys.exit(1)

    # 3. Инициализация Senet API
    senet = SenetAPI(
        mode=config.senet_mode,
        api_url=config.senet_api_url,
        api_key=config.senet_api_key,
    )

    # 4. Сборка Application с job_queue
    app: Application = Application.builder().token(config.bot_token).build()

    # Зависимости через bot_data
    app.bot_data["db_path"] = config.db_path
    app.bot_data["webapp_base_url"] = config.webapp_base_url
    app.bot_data["initial_admin_ids"] = config.initial_admin_ids
    app.bot_data["config"] = config
    app.bot_data["senet"] = senet

    # 5. Хендлеры
    _register_handlers(app)

    # 6. Регистрируем фоновые задачи
    if app.job_queue:
        # Первичная синхронизация цен через 5 секунд после старта
        app.job_queue.run_once(_job_sync_prices, when=5)
        # Затем каждый час
        app.job_queue.run_repeating(_job_sync_prices, interval=3600, first=3605)
        # Проверка запланированных рассылок каждую минуту
        app.job_queue.run_repeating(_job_scheduled_broadcasts, interval=60, first=30)
        # Поздравления именинников — каждый день в 08:00 UTC (13:00 Астана)
        from datetime import time as dtime

        app.job_queue.run_daily(_job_birthday_greetings, time=dtime(hour=8, minute=0))
        # Резервное копирование — каждый день в 03:00 UTC (08:00 Астана)
        app.job_queue.run_daily(_job_backup, time=dtime(hour=3, minute=0))
        logger.info("Фоновые задачи зарегистрированы.")
    else:
        logger.warning(
            "job_queue недоступен — автообновление цен и рассылки отключены."
        )

    logger.info("Бот запускается в режиме polling...")

    # 7. Запуск
 # Настройка Webhook для работы на BotHost
 PORT = 8443
 WEBHOOK_URL = "https://bot-1782577262-3322-illusionbaby.bothost.tech"  # Ссылка, которую дал вам BotHost

 app.run_webhook(
     listen="0.0.0.0",
     port=PORT,
     url_path="/webhook",
     webhook_url=f"{WEBHOOK_URL}/webhook",
     allowed_updates=["message", "callback_query"],
     drop_pending_updates=True
 )

if __name__ == "__main__":
    main()

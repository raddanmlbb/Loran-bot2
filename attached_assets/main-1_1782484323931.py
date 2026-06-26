"""
main.py — точка входа LORAN.CYBER Bot.

Порядок запуска:
  1. Загрузка конфигурации из переменных окружения.
  2. Синхронный запуск миграций БД.
  3. Сборка PTB Application.
  4. Регистрация хендлеров.
  5. run_polling().

Переменные окружения (обязательно):
  BOT_TOKEN — токен от @BotFather.

Переменные окружения (опционально):
  DB_PATH            — путь к SQLite (по умолчанию: data/loran.db)
  SENET_MODE         — "mock" или "live" (по умолчанию: "mock")
  WEBAPP_BASE_URL    — базовый URL Web App
  INITIAL_ADMIN_IDS  — Telegram ID через запятую
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
# Регистрация хендлеров
# ---------------------------------------------------------------------------


def _register_handlers(app: Application) -> None:
    """
    Регистрирует все хендлеры PTB.

    Порядок важен:
      1. Команды.
      2. WEB_APP_DATA — до текстовых хендлеров.
      3. Текстовые кнопки.

    Args:
        app: инициализированный PTB Application.
    """
    # Команды
    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("help",    cmd_help))
    app.add_handler(CommandHandler("profile", cmd_profile))

    # Данные от Web App — регистрируем ДО текстовых хендлеров
    app.add_handler(
        MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_webapp_data)
    )

    # Кнопки Reply-клавиатуры — точное совпадение текста
    app.add_handler(MessageHandler(filters.Text([MESSAGES["btn_booking"]]),  handle_booking))
    app.add_handler(MessageHandler(filters.Text([MESSAGES["btn_price"]]),    handle_price))
    app.add_handler(MessageHandler(filters.Text([MESSAGES["btn_news"]]),     handle_news))
    app.add_handler(MessageHandler(filters.Text([MESSAGES["btn_profile"]]),  handle_profile))
    app.add_handler(MessageHandler(filters.Text([MESSAGES["btn_address"]]),  handle_address))
    app.add_handler(MessageHandler(filters.Text([MESSAGES["btn_contacts"]]), handle_contacts))
    app.add_handler(MessageHandler(filters.Text([MESSAGES["btn_register"]]), handle_register))


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------


def main() -> None:
    """
    Инициализирует и запускает бота.

    Raises:
        SystemExit: при критической ошибке конфигурации или миграций.
    """
    # 1. Конфигурация
    try:
        config: Config = load_config()
    except ValueError as exc:
        logger.critical("Ошибка конфигурации: %s", exc)
        sys.exit(1)

    logger.info(
        "Конфигурация загружена. SENET_MODE=%s, DB_PATH=%s",
        config.senet_mode, config.db_path,
    )

    # 2. Миграции БД (синхронно, до старта PTB)
    try:
        run_migrations(config.db_path)
    except Exception as exc:
        logger.critical("Не удалось применить миграции БД: %s", exc)
        sys.exit(1)

    # 3. Сборка Application
    app: Application = Application.builder().token(config.bot_token).build()

    # Зависимости через bot_data — без глобальных переменных
    app.bot_data["db_path"]            = config.db_path
    app.bot_data["webapp_base_url"]    = config.webapp_base_url
    app.bot_data["initial_admin_ids"]  = config.initial_admin_ids
    app.bot_data["config"]             = config
    app.bot_data["senet"]              = SenetAPI(mode=config.senet_mode)

    # 4. Хендлеры
    _register_handlers(app)

    logger.info("Бот запускается в режиме polling...")

    # 5. Запуск (блокирует до SIGINT/SIGTERM, graceful shutdown встроен в PTB v20)
    app.run_polling(
        allowed_updates=["message", "callback_query"],
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()

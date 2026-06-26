"""
db/models.py — DDL-схемы всех таблиц базы данных LORAN.CYBER.

Соглашения:
  - Все поля времени хранятся в UTC.
  - Булевы значения: 0 / 1 (SQLite не имеет типа BOOLEAN).
  - Внешние ключи включены через PRAGMA в каждом соединении.

Версии миграций:
  v1 — базовые таблицы (Этап 1).
  v2 — таблица pending_registrations + индексы (Этап 1, правка).
"""

# ---------------------------------------------------------------------------
# Версионирование схемы
# ---------------------------------------------------------------------------

SCHEMA_VERSION_TABLE: str = """
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# ---------------------------------------------------------------------------
# Пользователи
# ---------------------------------------------------------------------------

USERS_TABLE: str = """
CREATE TABLE IF NOT EXISTS users (
    telegram_id               INTEGER PRIMARY KEY,
    login                     TEXT UNIQUE NOT NULL,
    phone                     TEXT,
    display_name              TEXT,

    -- День рождения и бонусная механика
    birthday                  DATE,
    birthday_source           TEXT,
    birthday_asked_at         DATE,
    birthday_ask_count        INTEGER DEFAULT 0,
    birthday_declined         INTEGER DEFAULT 0,
    last_birthday_bonus       DATE,

    -- Статус аккаунта
    status                    TEXT DEFAULT 'active',
    registered_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_activity             TIMESTAMP,
    banned_reason             TEXT,

    -- Административные поля
    is_admin                  INTEGER DEFAULT 0,
    admin_login               TEXT UNIQUE,
    admin_password_hash       TEXT,
    admin_level               TEXT,
    admin_session_token       TEXT,
    admin_session_expires     TIMESTAMP,
    admin_login_attempts      INTEGER DEFAULT 0,
    admin_locked_until        TIMESTAMP,

    -- Интеграция с Senet
    senet_user_id             TEXT,
    senet_login               TEXT,
    senet_verified            INTEGER DEFAULT 0,
    senet_verify_attempts     INTEGER DEFAULT 0,
    senet_verify_locked_until TIMESTAMP,

    -- Настройки пользователя
    language                  TEXT DEFAULT 'ru',
    theme                     TEXT DEFAULT 'auto'
);
"""

# ---------------------------------------------------------------------------
# Временные данные регистрации
# Хранит данные нового пользователя до завершения верификации Senet.
# Запись удаляется при успешной или неуспешной верификации.
# ---------------------------------------------------------------------------

PENDING_REGISTRATIONS_TABLE: str = """
CREATE TABLE IF NOT EXISTS pending_registrations (
    telegram_id   INTEGER PRIMARY KEY,
    login         TEXT NOT NULL,
    display_name  TEXT,
    phone         TEXT NOT NULL,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at    TIMESTAMP NOT NULL
);
"""
# Примечание: FOREIGN KEY на users намеренно отсутствует — запись создаётся
# до записи в users (для пользователей, чей логин найден в Senet).

# ---------------------------------------------------------------------------
# Бронирования
# ---------------------------------------------------------------------------

BOOKINGS_TABLE: str = """
CREATE TABLE IF NOT EXISTS bookings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id     INTEGER NOT NULL,
    booking_code    TEXT UNIQUE,
    date            DATE NOT NULL,
    time_from       TIME NOT NULL,
    time_to         TIME NOT NULL,
    total_price     INTEGER,
    status          TEXT DEFAULT 'confirmed',
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    cancelled_at    TIMESTAMP,
    cancelled_by    TEXT,
    cancel_reason   TEXT,
    reminder_sent   INTEGER DEFAULT 0,

    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
);
"""

BOOKING_PCS_TABLE: str = """
CREATE TABLE IF NOT EXISTS booking_pcs (
    booking_id      INTEGER NOT NULL,
    pc_id           INTEGER NOT NULL,
    zone            TEXT NOT NULL,
    price_per_pc    INTEGER,

    PRIMARY KEY (booking_id, pc_id),
    FOREIGN KEY (booking_id) REFERENCES bookings(id)
);
"""

# ---------------------------------------------------------------------------
# Публикации (новости, акции)
# ---------------------------------------------------------------------------

POSTS_TABLE: str = """
CREATE TABLE IF NOT EXISTS posts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    type            TEXT NOT NULL,
    title           TEXT NOT NULL,
    image_url       TEXT,
    body            TEXT NOT NULL,
    button_text     TEXT,
    button_action   TEXT,
    promo_code      TEXT,
    starts_at       DATE,
    expires_at      DATE,
    send_to_bot     INTEGER DEFAULT 0,
    target_group    TEXT DEFAULT 'all',
    status          TEXT DEFAULT 'published',
    admin_id        INTEGER,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (admin_id) REFERENCES users(telegram_id)
);
"""

# ---------------------------------------------------------------------------
# Рассылки
# ---------------------------------------------------------------------------

BROADCASTS_TABLE: str = """
CREATE TABLE IF NOT EXISTS broadcasts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id        INTEGER,
    type            TEXT,
    target_group    TEXT,
    title           TEXT,
    message_text    TEXT,
    image_url       TEXT,
    button_text     TEXT,
    button_action   TEXT,
    scheduled_at    TIMESTAMP,
    sent_count      INTEGER DEFAULT 0,
    failed_count    INTEGER DEFAULT 0,
    opens_count     INTEGER DEFAULT 0,
    clicks_count    INTEGER DEFAULT 0,
    status          TEXT DEFAULT 'pending',
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (admin_id) REFERENCES users(telegram_id)
);
"""

BROADCAST_RECIPIENTS_TABLE: str = """
CREATE TABLE IF NOT EXISTS broadcast_recipients (
    broadcast_id    INTEGER NOT NULL,
    telegram_id     INTEGER NOT NULL,
    status          TEXT,

    PRIMARY KEY (broadcast_id, telegram_id),
    FOREIGN KEY (broadcast_id) REFERENCES broadcasts(id),
    FOREIGN KEY (telegram_id)  REFERENCES users(telegram_id)
);
"""

# ---------------------------------------------------------------------------
# Журнал действий администраторов
# ---------------------------------------------------------------------------

ADMIN_LOGS_TABLE: str = """
CREATE TABLE IF NOT EXISTS admin_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id        INTEGER,
    action          TEXT,
    target_type     TEXT,
    target_id       TEXT,
    details         TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (admin_id) REFERENCES users(telegram_id)
);
"""

# ---------------------------------------------------------------------------
# Прайс-лист
# ---------------------------------------------------------------------------

PRICING_TABLE: str = """
CREATE TABLE IF NOT EXISTS pricing (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    zone            TEXT NOT NULL,
    package_name    TEXT NOT NULL,
    hours           INTEGER,
    price           INTEGER NOT NULL,
    fixed_start     TIME,
    fixed_end       TIME,
    is_popular      INTEGER DEFAULT 0,
    is_active       INTEGER DEFAULT 1,
    sort_order      INTEGER DEFAULT 0,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

PRICE_HISTORY_TABLE: str = """
CREATE TABLE IF NOT EXISTS price_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id        INTEGER,
    zone            TEXT,
    package_name    TEXT,
    old_price       INTEGER,
    new_price       INTEGER,
    changed_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (admin_id) REFERENCES users(telegram_id)
);
"""

# ---------------------------------------------------------------------------
# Коды верификации (привязка к Senet)
# ---------------------------------------------------------------------------

VERIFICATION_CODES_TABLE: str = """
CREATE TABLE IF NOT EXISTS verification_codes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    code            TEXT UNIQUE NOT NULL,
    telegram_id     INTEGER NOT NULL,
    senet_login     TEXT NOT NULL,
    status          TEXT DEFAULT 'pending',
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at      TIMESTAMP,
    used_at         TIMESTAMP
);
"""

# ---------------------------------------------------------------------------
# Лист ожидания
# ---------------------------------------------------------------------------

WAITLIST_TABLE: str = """
CREATE TABLE IF NOT EXISTS waitlist (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id     INTEGER NOT NULL,
    zone            TEXT,
    pc_count        INTEGER,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    notified        INTEGER DEFAULT 0,
    notified_at     TIMESTAMP,
    expires_at      TIMESTAMP,

    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
);
"""

# ---------------------------------------------------------------------------
# Индексы
# ---------------------------------------------------------------------------

INDEXES_V1: list[str] = [
    "CREATE INDEX IF NOT EXISTS idx_bookings_telegram_id  ON bookings(telegram_id);",
    "CREATE INDEX IF NOT EXISTS idx_bookings_date         ON bookings(date);",
    "CREATE INDEX IF NOT EXISTS idx_bookings_status       ON bookings(status);",
    "CREATE INDEX IF NOT EXISTS idx_posts_status          ON posts(status);",
    "CREATE INDEX IF NOT EXISTS idx_posts_type            ON posts(type);",
    "CREATE INDEX IF NOT EXISTS idx_verification_telegram ON verification_codes(telegram_id);",
    "CREATE INDEX IF NOT EXISTS idx_waitlist_telegram     ON waitlist(telegram_id);",
]

INDEXES_V2: list[str] = [
    "CREATE INDEX IF NOT EXISTS idx_pending_reg_telegram ON pending_registrations(telegram_id);",
    "CREATE INDEX IF NOT EXISTS idx_booking_pcs_pc_id    ON booking_pcs(pc_id);",
]

# ---------------------------------------------------------------------------
# Финальные списки миграций
# ---------------------------------------------------------------------------

MIGRATION_V1: list[str] = [
    SCHEMA_VERSION_TABLE,
    USERS_TABLE,
    BOOKINGS_TABLE,
    BOOKING_PCS_TABLE,
    POSTS_TABLE,
    BROADCASTS_TABLE,
    BROADCAST_RECIPIENTS_TABLE,
    ADMIN_LOGS_TABLE,
    PRICING_TABLE,
    PRICE_HISTORY_TABLE,
    VERIFICATION_CODES_TABLE,
    WAITLIST_TABLE,
    *INDEXES_V1,
]

MIGRATION_V2: list[str] = [
    PENDING_REGISTRATIONS_TABLE,
    *INDEXES_V2,
]

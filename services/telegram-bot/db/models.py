"""
db/models.py — DDL-схемы всех таблиц базы данных LORAN.CYBER.

Версии:
  v1 — базовые таблицы.
  v2 — pending_registrations + индексы.
  v3 — admin_sessions, club_contacts, обновлённые pricing и users (Этап 5).
"""

SCHEMA_VERSION_TABLE: str = """
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

USERS_TABLE: str = """
CREATE TABLE IF NOT EXISTS users (
    telegram_id               INTEGER PRIMARY KEY,
    login                     TEXT UNIQUE NOT NULL,
    phone                     TEXT,
    display_name              TEXT,

    birthday                  DATE,
    birthday_source           TEXT,
    birthday_asked_at         DATE,
    birthday_ask_count        INTEGER DEFAULT 0,
    birthday_declined         INTEGER DEFAULT 0,
    last_birthday_bonus       DATE,

    status                    TEXT DEFAULT 'active',
    registered_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_activity             TIMESTAMP,
    banned_reason             TEXT,

    is_admin                  INTEGER DEFAULT 0,
    admin_login               TEXT UNIQUE,
    admin_password_hash       TEXT,
    admin_level               TEXT DEFAULT 'regular',
    admin_session_token       TEXT,
    admin_session_expires     TIMESTAMP,
    admin_login_attempts      INTEGER DEFAULT 0,
    admin_locked_until        TIMESTAMP,

    senet_user_id             TEXT,
    senet_login               TEXT,
    senet_verified            INTEGER DEFAULT 0,
    senet_verify_attempts     INTEGER DEFAULT 0,
    senet_verify_locked_until TIMESTAMP,

    language                  TEXT DEFAULT 'ru',
    theme                     TEXT DEFAULT 'auto'
);
"""

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

PRICING_TABLE: str = """
CREATE TABLE IF NOT EXISTS pricing (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    zone            TEXT NOT NULL,
    package_id      TEXT NOT NULL,
    price           INTEGER NOT NULL,
    hours           INTEGER,
    fixed_start     TIME,
    fixed_end       TIME,
    is_popular      INTEGER DEFAULT 0,
    is_active       INTEGER DEFAULT 1,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    UNIQUE(zone, package_id)
);
"""

PRICE_HISTORY_TABLE: str = """
CREATE TABLE IF NOT EXISTS price_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id        INTEGER,
    zone            TEXT,
    package_id      TEXT,
    old_price       INTEGER,
    new_price       INTEGER,
    changed_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (admin_id) REFERENCES users(telegram_id)
);
"""

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

# Этап 5: контакты клуба (редактируются в админ-панели)
CLUB_CONTACTS_TABLE: str = """
CREATE TABLE IF NOT EXISTS club_contacts (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    phone       TEXT,
    whatsapp    TEXT,
    telegram    TEXT,
    instagram   TEXT,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_by  INTEGER
);
"""

# Индексы v1
INDEXES_V1: list[str] = [
    "CREATE INDEX IF NOT EXISTS idx_bookings_telegram_id  ON bookings(telegram_id);",
    "CREATE INDEX IF NOT EXISTS idx_bookings_date         ON bookings(date);",
    "CREATE INDEX IF NOT EXISTS idx_bookings_status       ON bookings(status);",
    "CREATE INDEX IF NOT EXISTS idx_posts_status          ON posts(status);",
    "CREATE INDEX IF NOT EXISTS idx_posts_type            ON posts(type);",
    "CREATE INDEX IF NOT EXISTS idx_verification_telegram ON verification_codes(telegram_id);",
    "CREATE INDEX IF NOT EXISTS idx_waitlist_telegram     ON waitlist(telegram_id);",
]

# Индексы v2
INDEXES_V2: list[str] = [
    "CREATE INDEX IF NOT EXISTS idx_pending_reg_telegram ON pending_registrations(telegram_id);",
    "CREATE INDEX IF NOT EXISTS idx_booking_pcs_pc_id    ON booking_pcs(pc_id);",
]

# Индексы v3
INDEXES_V3: list[str] = [
    "CREATE INDEX IF NOT EXISTS idx_admin_logs_admin_id  ON admin_logs(admin_id);",
    "CREATE INDEX IF NOT EXISTS idx_pricing_zone         ON pricing(zone);",
]

# Этап 6: миграция v4 — колонка post_id в broadcasts + индексы
INDEXES_V4: list[str] = [
    "CREATE INDEX IF NOT EXISTS idx_broadcasts_status      ON broadcasts(status);",
    "CREATE INDEX IF NOT EXISTS idx_broadcasts_scheduled   ON broadcasts(scheduled_at);",
    "CREATE INDEX IF NOT EXISTS idx_bcast_recipients_bid   ON broadcast_recipients(broadcast_id);",
    "CREATE INDEX IF NOT EXISTS idx_posts_created_at       ON posts(created_at);",
]

MIGRATION_V4: list[str] = [
    # post_id связывает рассылку с новостью
    "ALTER TABLE broadcasts ADD COLUMN post_id INTEGER REFERENCES posts(id);",
    # sent_at — момент реальной отправки
    "ALTER TABLE broadcasts ADD COLUMN sent_at TIMESTAMP;",
    # total_recipients — сколько получателей определено при запуске
    "ALTER TABLE broadcasts ADD COLUMN total_recipients INTEGER DEFAULT 0;",
    *INDEXES_V4,
]

# Этап 7: таблица промокодов ко дням рождения
BIRTHDAY_PROMOS_TABLE: str = """
CREATE TABLE IF NOT EXISTS birthday_promos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER NOT NULL,
    code        TEXT UNIQUE NOT NULL,
    year        INTEGER NOT NULL,
    discount    INTEGER DEFAULT 20,
    expires_at  DATE NOT NULL,
    used_at     TIMESTAMP,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
);
"""

INDEXES_V5: list[str] = [
    "CREATE INDEX IF NOT EXISTS idx_birthday_promos_tid  ON birthday_promos(telegram_id);",
    "CREATE INDEX IF NOT EXISTS idx_birthday_promos_code ON birthday_promos(code);",
]

MIGRATION_V5: list[str] = [
    BIRTHDAY_PROMOS_TABLE,
    *INDEXES_V5,
]

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

# Этап 5: таблица контактов клуба
MIGRATION_V3: list[str] = [
    CLUB_CONTACTS_TABLE,
    *INDEXES_V3,
    # Начальная запись контактов
    "INSERT OR IGNORE INTO club_contacts (id) VALUES (1);",
]

import sqlite3
from database import get_connection

# Текущая версия схемы базы данных
CURRENT_DB_VERSION = 3


def init_schema():
    """Инициализация и миграция схемы БД до CURRENT_DB_VERSION.

    Первый запуск (нет таблицы db_version): полное создание всех таблиц
    сразу на актуальной версии. Миграции не выполняются — они не нужны.

    Существующая БД с актуальной версией: ранний возврат, ничего не делается
    (обычный путь — база почти всегда уже есть и актуальна).

    Существующая БД со старой версией: перед миграциями гарантируется наличие
    всех таблиц (CREATE IF NOT EXISTS), затем применяются инкрементальные
    ALTER-миграции.
    """
    conn = get_connection()
    cursor = conn.cursor()

    # Наличие db_version — индикатор существующей БД
    cursor.execute("""
        SELECT name FROM sqlite_master
        WHERE type='table' AND name='db_version'
    """)
    if not cursor.fetchone():
        # ─── Первый запуск: полное создание схемы ───
        _create_all_tables(cursor)
        cursor.execute("CREATE TABLE db_version (version INTEGER)")
        cursor.execute("INSERT INTO db_version VALUES (?)", (CURRENT_DB_VERSION,))
        conn.commit()
        conn.close()
        print(f"[init_schema] БД создана на версии {CURRENT_DB_VERSION}")
        return

    # ─── Существующая БД ───
    cursor.execute("SELECT version FROM db_version LIMIT 1")
    row = cursor.fetchone()
    if row is None:
        # db_version пуста — инициализируем на текущей без миграций
        cursor.execute("INSERT INTO db_version VALUES (?)", (CURRENT_DB_VERSION,))
        conn.commit()
        conn.close()
        print(f"[init_schema] Версия БД инициализирована на {CURRENT_DB_VERSION}")
        return

    current = row["version"]
    if current >= CURRENT_DB_VERSION:
        conn.close()
        print(f"[init_schema] Версия БД актуальна: {current}")
        return

    print(f"[init_schema] Текущая версия БД: {current}, целевая: {CURRENT_DB_VERSION}")

    # Перед миграциями убедиться, что все таблицы существуют
    # (на пути обновления старой БД некоторые таблицы могли отсутствовать)
    _create_all_tables(cursor)
    conn.commit()

    _apply_migrations(cursor, current)
    conn.commit()
    conn.close()
    print(f"[init_schema] Миграции завершены. Версия: {CURRENT_DB_VERSION}")


def _create_all_tables(cursor):
    """Создать все таблицы схемы (CREATE IF NOT EXISTS).

    Используется как при первом запуске, так и перед миграциями старой БД —
    гарантирует наличие всех таблиц. Идемпотентно.
    """
    # ─── 0. Таблица валют ───
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS currencies (
            id INTEGER PRIMARY KEY,
            code TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL
        )
    """)
    # Сидирование
    currencies = [
        (1, 'RUB', 'Российский рубль'),
        (2, 'USD', 'Доллар США'),
        (3, 'EUR', 'Евро'),
        (4, 'CNY', 'Китайский юань'),
    ]
    for c in currencies:
        cursor.execute(
            "INSERT OR IGNORE INTO currencies (id, code, name) VALUES (?, ?, ?)", c
        )

    # ─── 1. Таблица счетов ───
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            account_number TEXT DEFAULT '',
            broker_type TEXT NOT NULL DEFAULT 'stock',
            active INTEGER NOT NULL DEFAULT 1,
            currency_id INTEGER NOT NULL DEFAULT 1,
            balance REAL NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY (currency_id) REFERENCES currencies(id)
        )
    """)

    # ─── 2. Таблица активов ───
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS assets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            name TEXT DEFAULT '',
            asset_type TEXT NOT NULL,
            quantity REAL NOT NULL,
            avg_price REAL NOT NULL,
            current_price REAL DEFAULT 0,
            last_update TEXT,
            broker_id INTEGER,
            purchase_date TEXT NOT NULL,
            created_at TEXT NOT NULL,
            currency_id INTEGER NOT NULL DEFAULT 1,
            face_value REAL DEFAULT 1000,
            lot_size INTEGER DEFAULT 1,
            lot_value REAL DEFAULT 1000,
            list_level INTEGER,
            coupon_percent REAL,
            FOREIGN KEY (broker_id) REFERENCES accounts(id),
            FOREIGN KEY (currency_id) REFERENCES currencies(id)
        )
    """)

    # ─── 0b. Реестр тикеров ───
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ticker_names (
            ticker TEXT PRIMARY KEY,
            name TEXT NOT NULL DEFAULT ''
        )
    """)
    # Миграция: заполнить из существующих активов
    try:
        cursor.execute("""
            INSERT OR IGNORE INTO ticker_names (ticker, name)
            SELECT ticker, name FROM assets
            WHERE ticker != '' AND name IS NOT NULL AND name != ''
        """)
    except Exception:
        pass

    # ─── 3. Таблица покупок (лог каждой докупки) ───
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS buys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asset_id INTEGER,
            ticker TEXT NOT NULL,
            quantity REAL NOT NULL,
            price REAL NOT NULL,
            currency_id INTEGER NOT NULL DEFAULT 1,
            broker_id INTEGER,
            buy_date TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (asset_id) REFERENCES assets(id),
            FOREIGN KEY (broker_id) REFERENCES accounts(id),
            FOREIGN KEY (currency_id) REFERENCES currencies(id)
        )
    """)

    # ─── 4. Таблица настроек ───
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            setting_key TEXT PRIMARY KEY,
            setting_value TEXT NOT NULL,
            updated_at TEXT
        )
    """)

    # ─── 5. Таблица истории портфеля (глобальный срез) ───
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS portfolio_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            total_value REAL NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    # ─── 6. Таблица срезов портфеля (по счёту, 1 запись в счёт за месяц) ───
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            account_id INTEGER NOT NULL,
            balance_rub REAL DEFAULT 0,
            assets_value_rub REAL DEFAULT 0,
            portfolio_total_rub REAL DEFAULT 0,
            deposits REAL DEFAULT 0,
            withdrawals REAL DEFAULT 0,
            dividends REAL DEFAULT 0,
            coupons REAL DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (account_id) REFERENCES accounts(id)
        )
    """)

    cursor.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_snapshots_month_account
        ON snapshots (strftime('%Y-%m', date), account_id)
    """)

    # ─── 7. Таблица транзакций ───
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            tx_type TEXT NOT NULL,
            account_id INTEGER,
            ticker TEXT DEFAULT '',
            amount REAL NOT NULL,
            currency_id INTEGER NOT NULL DEFAULT 1,
            notes TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            asset_id INTEGER,
            qty REAL,
            price REAL,
            profit REAL,
            FOREIGN KEY (account_id) REFERENCES accounts(id),
            FOREIGN KEY (asset_id) REFERENCES assets(id),
            FOREIGN KEY (currency_id) REFERENCES currencies(id)
        )
    """)

    # ─── 8. Таблица деталей среза ───
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS snapshot_assets (
            snapshot_id INTEGER NOT NULL,
            asset_id INTEGER,
            ticker TEXT NOT NULL,
            name TEXT DEFAULT '',
            asset_type TEXT DEFAULT '',
            quantity REAL NOT NULL,
            avg_price REAL NOT NULL,
            current_price REAL NOT NULL,
            currency_id INTEGER NOT NULL DEFAULT 1,
            fx_rate REAL DEFAULT 1,
            value_rub REAL NOT NULL,
            FOREIGN KEY (snapshot_id) REFERENCES snapshots(id) ON DELETE CASCADE,
            FOREIGN KEY (asset_id) REFERENCES assets(id),
            FOREIGN KEY (currency_id) REFERENCES currencies(id)
        )
    """)


def _apply_migrations(cursor, current):
    """Применить инкрементальные миграции до CURRENT_DB_VERSION.

    current — текущая версия схемы в БД. Каждая ступень обновляет db_version.
    """
    if current < 2:
        cursor.execute("ALTER TABLE assets ADD COLUMN face_value REAL DEFAULT 1000")
        cursor.execute("ALTER TABLE assets ADD COLUMN lot_size INTEGER DEFAULT 1")
        cursor.execute("ALTER TABLE assets ADD COLUMN lot_value REAL DEFAULT 1000")
        cursor.execute("ALTER TABLE assets ADD COLUMN list_level INTEGER")
        cursor.execute("ALTER TABLE assets ADD COLUMN coupon_percent REAL")
        cursor.execute("UPDATE db_version SET version = 2")
        current = 2
        print("[init_schema] Применена миграция до версии 2")

    if current < 3:
        cursor.execute("ALTER TABLE ticker_names ADD COLUMN asset_type TEXT NOT NULL DEFAULT ''")
        cursor.execute("ALTER TABLE ticker_names ADD COLUMN lot_size INTEGER NOT NULL DEFAULT 1")
        cursor.execute("ALTER TABLE ticker_names ADD COLUMN currency TEXT NOT NULL DEFAULT ''")

        # Сидирование: тип и лотность из assets
        cursor.execute("""
            UPDATE ticker_names
            SET asset_type = (SELECT a.asset_type FROM assets a
                              WHERE a.ticker = ticker_names.ticker LIMIT 1)
            WHERE asset_type = ''
              AND EXISTS (SELECT 1 FROM assets a WHERE a.ticker = ticker_names.ticker)
        """)
        cursor.execute("""
            UPDATE ticker_names
            SET lot_size = (SELECT a.lot_size FROM assets a
                            WHERE a.ticker = ticker_names.ticker
                              AND a.lot_size IS NOT NULL AND a.lot_size > 0 LIMIT 1)
            WHERE lot_size = 1
              AND EXISTS (SELECT 1 FROM assets a WHERE a.ticker = ticker_names.ticker
                           AND a.lot_size IS NOT NULL AND a.lot_size > 0)
        """)

        # Сидирование: валюта из assets.currency_id → currencies.code
        cursor.execute("""
            UPDATE ticker_names
            SET currency = (SELECT c.code FROM assets a
                            JOIN currencies c ON a.currency_id = c.id
                            WHERE a.ticker = ticker_names.ticker LIMIT 1)
            WHERE currency = ''
              AND EXISTS (SELECT 1 FROM assets a WHERE a.ticker = ticker_names.ticker)
        """)

        cursor.execute("UPDATE db_version SET version = 3")
        current = 3
        print("[init_schema] Применена миграция до версии 3")

import sqlite3
import sys
import os
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
import calendar

if getattr(sys, 'frozen', False):
    _APP_DIR = os.path.dirname(sys.executable)
else:
    _APP_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_APP_DIR, "investments.db")

# Текущая версия схемы базы данных
CURRENT_DB_VERSION = 2

_TYPE_MAP = {
    'stock': 'Мос.Биржа',
    'crypto': 'Крипто биржа',
    'Мос.Биржа': 'Мос.Биржа',
    'Крипто биржа': 'Крипто биржа',
}

# Кэш: id → code
_currency_code_cache = None


def set_db_path(path):
    """Изменить путь к базе данных."""
    global DB_PATH
    DB_PATH = path


def get_db_path():
    """Получить текущий путь к базе данных."""
    return DB_PATH


def get_connection():
    """Подключение к базе данных."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def backup_database(dest_path):
    """Создать резервную копию текущей БД в dest_path."""
    src = sqlite3.connect(DB_PATH)
    dst = sqlite3.connect(dest_path)
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()


def round_price(value):
    """Математическое округление до 2 знаков (от 5 вверх)."""
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def display_type(broker_type):
    """Отображаемый тип счёта."""
    return _TYPE_MAP.get(broker_type, broker_type)


# ================================================================
#  Helpers — currencies
# ================================================================

def _ensure_currency_cache():
    global _currency_code_cache
    if _currency_code_cache is None:
        _currency_code_cache = {}
        try:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT id, code, name FROM currencies")
            for row in cursor.fetchall():
                _currency_code_cache[row["id"]] = {"code": row["code"], "name": row["name"]}
            conn.close()
        except Exception:
            pass
    return _currency_code_cache


def get_currencies():
    """Получить все валюты: [(id, code, name), ...]."""
    _ensure_currency_cache()
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, code, name FROM currencies ORDER BY id")
    rows = cursor.fetchall()
    conn.close()
    return rows


def get_currency_code(currency_id):
    """Получить код валюты (RUB, USD, …) по ID."""
    _ensure_currency_cache()
    return _currency_code_cache.get(currency_id, {}).get("code", "RUB")


def get_currency_id(code):
    """Получить ID валюты по коду (RUB → 1, …)."""
    _ensure_currency_cache()
    for cid, info in _currency_code_cache.items():
        if info["code"] == code:
            return cid
    # fallback — если кэш не загрузился, вернём RUB по умолчанию
    return 1


# ================================================================
#  Реестр тикеров
# ================================================================

def upsert_ticker_name(ticker, name):
    """Добавить или обновить запись в реестре тикеров."""
    if not ticker or not name:
        return
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO ticker_names (ticker, name) VALUES (?, ?)
        ON CONFLICT(ticker) DO UPDATE SET name = ?
        WHERE excluded.name != '' AND ticker_names.name = ''
    """, (ticker.strip().upper(), name.strip(), name.strip()))
    conn.commit()
    conn.close()


def import_ticker_names(rows):
    """Массовый импорт/обновление записей тикеров. rows: [(ticker, name), ...]."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        for ticker, name in rows:
            ticker = ticker.strip().upper()
            name = name.strip() if name else ''
            if not ticker:
                continue
            cursor.execute("""
                INSERT INTO ticker_names (ticker, name) VALUES (?, ?)
                ON CONFLICT(ticker) DO UPDATE SET name = excluded.name
            """, (ticker, name))
        count = len([1 for t, n in rows if t.strip()])
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()
    return count


def get_all_ticker_names():
    """Получить все записи реестра: [(ticker, name), ...]."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT ticker, name FROM ticker_names ORDER BY ticker")
    rows = cursor.fetchall()
    conn.close()
    return rows


def update_ticker_name(ticker, name):
    """Обновить имя тикера."""
    conn = get_connection()
    cursor = conn.cursor()
    ticker = str(ticker).strip().upper()
    name = str(name).strip() if name else ''
    cursor.execute("UPDATE ticker_names SET name = ? WHERE ticker = ?", (name, ticker))
    conn.commit()
    conn.close()


def add_ticker_name(ticker, name):
    """Добавить новую запись в реестр тикеров."""
    conn = get_connection()
    cursor = conn.cursor()
    ticker = str(ticker).strip().upper()
    name = str(name).strip() if name else ''
    try:
        cursor.execute("INSERT INTO ticker_names (ticker, name) VALUES (?, ?)",
                       (ticker, name))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise ValueError("Тикер уже существует")
    conn.close()


def delete_ticker_name(ticker):
    """Удалить запись из реестра."""
    conn = get_connection()
    cursor = conn.cursor()
    ticker = str(ticker).strip().upper()
    cursor.execute("DELETE FROM ticker_names WHERE ticker = ?", (ticker,))
    conn.commit()
    conn.close()


def rename_ticker(old_ticker, new_ticker, name):
    """Переименовать тикер с каскадным обновлением всех связанных таблиц."""
    conn = get_connection()
    cursor = conn.cursor()
    old_ticker = str(old_ticker).strip().upper()
    new_ticker = str(new_ticker).strip().upper()
    name = str(name).strip() if name else ''
    try:
        if old_ticker != new_ticker:
            cursor.execute("SELECT ticker FROM ticker_names WHERE ticker = ?", (new_ticker,))
            if cursor.fetchone():
                raise ValueError(f"Тикер {new_ticker} уже существует")
            for table in ('assets', 'buys', 'transactions', 'snapshot_assets'):
                cursor.execute(f"UPDATE {table} SET ticker = ? WHERE ticker = ?", (new_ticker, old_ticker))
            cursor.execute("UPDATE ticker_names SET ticker = ?, name = ? WHERE ticker = ?",
                           (new_ticker, name, old_ticker))
        else:
            cursor.execute("UPDATE ticker_names SET name = ? WHERE ticker = ?", (name, old_ticker))
    except Exception as e:
        conn.rollback()
        raise e
    conn.commit()
    conn.close()


def get_ticker_name(ticker):
    """Получить имя тикера из реестра."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM ticker_names WHERE ticker = ?", (str(ticker).strip().upper(),))
    row = cursor.fetchone()
    conn.close()
    return row["name"] if row else None


# ================================================================
#  init_db
# ================================================================

def init_db():
    """Инициализация таблиц базы данных."""
    conn = get_connection()
    cursor = conn.cursor()

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

    # ─── 9. Таблица версии схемы БД ───
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS db_version (
            version INTEGER
        )
    """)
    cursor.execute("SELECT COUNT(*) as cnt FROM db_version")
    if cursor.fetchone()["cnt"] == 0:
        cursor.execute("INSERT INTO db_version VALUES (?)", (CURRENT_DB_VERSION,))

    conn.commit()
    conn.close()


# ================================================================
#  Migrations
# ================================================================

def migrate_db():
    """Проверить версию схемы БД и применить миграции при необходимости."""
    conn = get_connection()
    cursor = conn.cursor()

    # Проверить наличие таблицы db_version
    cursor.execute("""
        SELECT name FROM sqlite_master
        WHERE type='table' AND name='db_version'
    """)
    if not cursor.fetchone():
        # Таблицы нет — создаём (база без версии)
        cursor.execute("CREATE TABLE db_version (version INTEGER)")
        cursor.execute("INSERT INTO db_version VALUES (?)", (CURRENT_DB_VERSION,))
        conn.commit()
        conn.close()
        print(f"[migrate_db] Схема инициализирована на версии {CURRENT_DB_VERSION}")
        return

    cursor.execute("SELECT version FROM db_version LIMIT 1")
    row = cursor.fetchone()
    current = row["version"] if row else 0

    if current >= CURRENT_DB_VERSION:
        conn.close()
        print(f"[migrate_db] Версия БД актуальна: {current}")
        return

    print(f"[migrate_db] Текущая версия БД: {current}, целевая: {CURRENT_DB_VERSION}")

    # ─── Миграции (применяются по очереди) ───
    if current < 2:
        cursor.execute("ALTER TABLE assets ADD COLUMN face_value REAL DEFAULT 1000")
        cursor.execute("ALTER TABLE assets ADD COLUMN lot_size INTEGER DEFAULT 1")
        cursor.execute("ALTER TABLE assets ADD COLUMN lot_value REAL DEFAULT 1000")
        cursor.execute("ALTER TABLE assets ADD COLUMN list_level INTEGER")
        cursor.execute("ALTER TABLE assets ADD COLUMN coupon_percent REAL")
        cursor.execute("UPDATE db_version SET version = 2")
        current = 2
        print("[migrate_db] Применена миграция до версии 2")

    conn.commit()
    conn.close()
    print(f"[migrate_db] Миграции завершены. Версия: {current}")


# ================================================================
#  Accounts
# ================================================================

def get_all_accounts():
    """Получить все счёта с currency_code."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT a.id, a.name, a.account_number, a.broker_type, a.active,
               a.currency_id, a.balance, a.created_at,
               c.code AS currency_code
        FROM accounts a
        LEFT JOIN currencies c ON a.currency_id = c.id
        ORDER BY a.name
    """)
    rows = cursor.fetchall()
    conn.close()
    return rows


def get_account(account_id):
    """Получить счёт по ID."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT a.id, a.name, a.account_number, a.broker_type, a.active,
               a.currency_id, a.balance, a.created_at,
               c.code AS currency_code
        FROM accounts a
        LEFT JOIN currencies c ON a.currency_id = c.id
        WHERE a.id = ?
    """, (account_id,))
    row = cursor.fetchone()
    conn.close()
    return row


def add_account(name, account_number='', broker_type='stock', currency_code='RUB', balance=0):
    """Добавить счёт. Возвращает account_id."""
    currency_id = get_currency_id(currency_code)
    conn = get_connection()
    cursor = conn.cursor()
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("""
        INSERT INTO accounts (name, account_number, broker_type, active, currency_id, balance, created_at)
        VALUES (?, ?, ?, 1, ?, ?, ?)
    """, (name, account_number, broker_type, currency_id, balance, created_at))
    conn.commit()
    account_id = cursor.lastrowid
    conn.close()
    return account_id


def update_account(account_id, name, account_number='', broker_type='stock',
                   currency_code='RUB', balance=0, active=1):
    """Обновить данные счёта."""
    currency_id = get_currency_id(currency_code)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE accounts
        SET name = ?, account_number = ?, broker_type = ?, active = ?,
            currency_id = ?, balance = ?
        WHERE id = ?
    """, (name, account_number, broker_type, active, currency_id, balance, account_id))
    conn.commit()
    conn.close()


def delete_account(account_id):
    """Удалить счёт."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as cnt FROM assets WHERE broker_id = ?", (account_id,))
    row = cursor.fetchone()
    if row["cnt"] > 0:
        conn.close()
        return False
    cursor.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
    conn.commit()
    conn.close()
    return True


def toggle_account_active(account_id):
    """Переключить статус active счёта."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT active FROM accounts WHERE id = ?", (account_id,))
    row = cursor.fetchone()
    if row:
        new_active = 0 if row["active"] == 1 else 1
        cursor.execute("UPDATE accounts SET active = ? WHERE id = ?", (new_active, account_id))
    conn.commit()
    conn.close()
    return new_active


def get_account_balance(account_id):
    """Получить баланс счёта."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM accounts WHERE id = ?", (account_id,))
    row = cursor.fetchone()
    balance = row["balance"] if row else 0
    conn.close()
    return balance


def set_account_balance(account_id, balance):
    """Установить баланс счёта."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE accounts SET balance = ? WHERE id = ?", (balance, account_id))
    conn.commit()
    conn.close()


def change_account_balance(account_id, delta, currency_code='RUB'):
    """Изменить баланс счёта на delta."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM accounts WHERE id = ?", (account_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return False, "Счёт не найден", 0
    old_bal = row["balance"]
    new_bal = round_price(old_bal + delta)
    if new_bal < 0:
        conn.close()
        return False, f"Недостаточно средств на счёте (свободно: {old_bal:.2f} {currency_code})", old_bal
    cursor.execute("UPDATE accounts SET balance = ? WHERE id = ?", (new_bal, account_id))
    conn.commit()
    conn.close()
    return True, f"Баланс: {old_bal:.2f} → {new_bal:.2f} {currency_code}", new_bal


# ================================================================
#  Transactions
# ================================================================

def add_transaction(tx_type, account_id, amount, currency_id, ticker='', notes='',
                    tx_date=None, asset_id=None, qty=None, price=None, profit=None):
    """Добавить запись в транзакции."""
    conn = get_connection()
    cursor = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if tx_date is None:
        tx_date = datetime.now().strftime("%Y-%m-%d")
    cursor.execute("""
        INSERT INTO transactions
            (date, tx_type, account_id, ticker, amount, currency_id, notes, created_at,
             asset_id, qty, price, profit)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (tx_date, tx_type, account_id, ticker, amount, currency_id, notes, now,
          asset_id, qty, price, profit))
    conn.commit()
    tx_id = cursor.lastrowid
    conn.close()
    return tx_id


def _build_transactions_where(account_id=None, tx_type=None, year=None, month=None):
    """Собрать WHERE-часть и параметры для запросов к transactions."""
    where = "WHERE 1=1"
    params = []
    if account_id is not None:
        where += " AND t.account_id = ?"
        params.append(account_id)
    if tx_type is not None:
        where += " AND t.tx_type = ?"
        params.append(tx_type)
    if year is not None:
        where += " AND strftime('%Y', t.date) = ?"
        params.append(str(year))
    if month is not None:
        where += " AND strftime('%m', t.date) = ?"
        params.append(f"{int(month):02d}")
    return where, params


def get_transactions_count(account_id=None, tx_type=None, year=None, month=None):
    """Возвратить общее количество транзакций, попавших под фильтры."""
    where, params = _build_transactions_where(account_id, tx_type, year, month)
    conn = get_connection()
    cursor = conn.cursor()
    query = f"""
        SELECT COUNT(*) as cnt
        FROM transactions t
        {where}
    """
    cursor.execute(query, params)
    row = cursor.fetchone()
    conn.close()
    return row["cnt"]


def get_transaction_years():
    """Возвратить список уникальных лет из транзакций + текущий год, по убыванию."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT strftime('%Y', t.date) as y FROM transactions t ORDER BY y DESC")
    rows = [r["y"] for r in cursor.fetchall()]
    current = str(datetime.now().year)
    if current not in rows:
        rows.insert(0, current)
    conn.close()
    return rows


def get_all_transactions(account_id=None, tx_type=None, year=None, month=None, limit=500, offset=0):
    """Получить все транзакции с именем счёта и кодом валюты."""
    where, params = _build_transactions_where(account_id, tx_type, year, month)
    conn = get_connection()
    cursor = conn.cursor()
    query = f"""
        SELECT t.id, t.date, t.tx_type, t.account_id, a.name as account_name,
               t.ticker, t.amount, c.code AS currency_code, t.notes, t.created_at, am.name as asset_name
        FROM transactions t
        LEFT JOIN accounts a ON t.account_id = a.id
        LEFT JOIN currencies c ON t.currency_id = c.id
        LEFT JOIN (
            SELECT ticker, name FROM assets
            WHERE name IS NOT NULL AND name != ''
            GROUP BY ticker
        ) am ON t.ticker = am.ticker
        {where}
        ORDER BY t.date DESC, t.id DESC
        LIMIT ? OFFSET ?
    """
    params.extend([limit, offset])
    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()
    return rows


def get_account_transactions(account_id):
    """Получить транзакции конкретного счёта."""
    return get_all_transactions(account_id=account_id)


def add_transaction_internal(cursor, tx_type, account_id, amount, currency_id, ticker='',
                             notes='', tx_date=None, asset_id=None, qty=None, price=None, profit=None):
    """Добавить транзакцию напрямую в cursor."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if tx_date is None:
        tx_date = datetime.now().strftime("%Y-%m-%d")
    cursor.execute("""
        INSERT INTO transactions
            (date, tx_type, account_id, ticker, amount, currency_id, notes, created_at,
             asset_id, qty, price, profit)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (tx_date, tx_type, account_id, ticker, amount, currency_id, notes, now,
          asset_id, qty, price, profit))


# ================================================================
#  Assets
# ================================================================

def compute_amount_in_account_currency(amount, asset_code, account_code, rates):
    """Конвертировать сумму в валюту счёта."""
    if asset_code == account_code:
        return amount
    if asset_code == "USD":
        return amount * rates.get("USD", 90.0)
    elif asset_code == "EUR":
        return amount * rates.get("EUR", 100.0)
    elif asset_code == "CNY":
        return amount * rates.get("CNY", 12.0)
    return amount


def add_asset(ticker, asset_type, quantity, price, purchase_date, account_id=None,
              name='', currency_code='RUB'):
    """Добавление актива."""
    currency_id = get_currency_id(currency_code)
    conn = get_connection()
    cursor = conn.cursor()
    price = round_price(price)
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Регистрируем тикер в справочнике
    upsert_ticker_name(ticker, name)

    if asset_type == "облигация":
        purchase_sum = quantity * price * 1000 / 100
    else:
        purchase_sum = quantity * price

    acc_currency_id = currency_id
    if account_id is not None:
        cursor.execute(
            "SELECT currency_id, balance FROM accounts WHERE id = ?", (account_id,)
        )
        acc_row = cursor.fetchone()
        if acc_row:
            acc_currency_id = acc_row["currency_id"] or 1
            acc_balance = acc_row["balance"]
            acc_code = get_currency_code(acc_currency_id)
            asset_code = get_currency_code(currency_id)
            rates = _get_rates_from_db(cursor)
            amount_in_acc_currency = compute_amount_in_account_currency(
                purchase_sum, asset_code, acc_code, rates
            )
            if acc_balance < amount_in_acc_currency:
                conn.close()
                return None, False, (
                    f"Недостаточно средств: нужно {amount_in_acc_currency:.2f} {acc_code}, "
                    f"на счёте {acc_balance:.2f} {acc_code}"
                )
            new_balance = round_price(acc_balance - amount_in_acc_currency)
            cursor.execute("UPDATE accounts SET balance = ? WHERE id = ?", (new_balance, account_id))

    cursor.execute("""
        INSERT INTO assets
            (ticker, name, asset_type, quantity, avg_price, broker_id, purchase_date, created_at, currency_id, face_value, lot_size, lot_value, list_level)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1000, 1, 1000, NULL)
    """, (ticker, name, asset_type, quantity, price, account_id, purchase_date, created_at, currency_id))
    asset_id = cursor.lastrowid

    if account_id is not None:
        acc_code = get_currency_code(acc_currency_id)
        asset_code = get_currency_code(currency_id)
        rates = _get_rates_from_db(cursor)
        amount_in_acc_currency = compute_amount_in_account_currency(
            purchase_sum, asset_code, acc_code, rates
        )
        add_transaction_internal(cursor, 'покупка', account_id, amount_in_acc_currency,
                                 acc_currency_id, ticker,
                                 f"Покупка {quantity}×{ticker} по {price:.2f}",
                                 asset_id=asset_id, qty=quantity, price=price, profit=None)

    conn.commit()
    conn.close()
    return asset_id, True, "Актив добавлен"


def _get_rates_from_db(cursor):
    """Вспомогательная: получить курсы валют."""
    rates = {"RUB": 1.0, "USD": 90.0, "EUR": 100.0, "CNY": 12.0}
    try:
        cursor.execute("SELECT setting_value FROM settings WHERE setting_key = 'usd_rub_rate'")
        row = cursor.fetchone()
        if row:
            rates["USD"] = float(row["setting_value"])
        cursor.execute("SELECT setting_value FROM settings WHERE setting_key = 'eur_rub_rate'")
        row = cursor.fetchone()
        if row:
            rates["EUR"] = float(row["setting_value"])
        cursor.execute("SELECT setting_value FROM settings WHERE setting_key = 'cny_rub_rate'")
        row = cursor.fetchone()
        if row:
            rates["CNY"] = float(row["setting_value"])
    except Exception:
        pass
    return rates


def get_all_assets(broker_id=None):
    """Получить все активы."""
    conn = get_connection()
    cursor = conn.cursor()
    if broker_id:
        cursor.execute("""
            SELECT a.*, c.code AS currency_code
            FROM assets a
            LEFT JOIN currencies c ON a.currency_id = c.id
            WHERE a.broker_id = ?
            ORDER BY a.created_at DESC
        """, (broker_id,))
    else:
        cursor.execute("""
            SELECT a.*, c.code AS currency_code
            FROM assets a
            LEFT JOIN currencies c ON a.currency_id = c.id
            ORDER BY a.created_at DESC
        """)
    rows = cursor.fetchall()
    conn.close()
    return rows


def get_assets_by_broker(broker_id):
    """Получить активы конкретного счёта."""
    return get_all_assets(broker_id)


def get_asset(asset_id):
    """Получить актив по ID."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT a.*, c.code AS currency_code
        FROM assets a
        LEFT JOIN currencies c ON a.currency_id = c.id
        WHERE a.id = ?
    """, (asset_id,))
    row = cursor.fetchone()
    conn.close()
    return row


def update_asset(asset_id, ticker, asset_type, quantity, price, purchase_date,
                 broker_id=None, name='', currency_code='RUB'):
    """Обновить данные актива."""
    currency_id = get_currency_id(currency_code)
    conn = get_connection()
    cursor = conn.cursor()
    price = round_price(price)
    # При редактировании пополняем реестр тикеров (защита от пустых значений внутри функции)
    upsert_ticker_name(ticker, name)
    cursor.execute("""
        UPDATE assets
        SET ticker = ?, name = ?, asset_type = ?, quantity = ?, avg_price = ?,
            broker_id = ?, purchase_date = ?, currency_id = ?
        WHERE id = ?
    """, (ticker, name, asset_type, quantity, price, broker_id, purchase_date, currency_id, asset_id))
    conn.commit()
    conn.close()


def remove_asset(asset_id):
    """Удалить актив по ID."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE snapshot_assets SET asset_id = NULL WHERE asset_id = ?", (asset_id,))
    cursor.execute("UPDATE transactions SET asset_id = NULL WHERE asset_id = ?", (asset_id,))
    cursor.execute("UPDATE buys SET asset_id = NULL WHERE asset_id = ?", (asset_id,))
    cursor.execute("DELETE FROM assets WHERE id = ?", (asset_id,))
    conn.commit()
    conn.close()


def sell_asset(asset_id, sell_price, sell_date, quantity=None):
    """Продажа актива."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT a.*, c.code AS currency_code
        FROM assets a
        LEFT JOIN currencies c ON a.currency_id = c.id
        WHERE a.id = ?
    """, (asset_id,))
    asset = cursor.fetchone()
    if not asset:
        conn.close()
        return None, False, "Актив не найден"

    asset_quantity = asset["quantity"]
    sold_qty = quantity if quantity is not None else asset_quantity
    if sold_qty > asset_quantity:
        sold_qty = asset_quantity

    if asset["asset_type"] == "облигация":
        fv = asset["face_value"] or 1000
        sell_sum = sell_price * sold_qty * fv / 100
        profit = (sell_price - asset["avg_price"]) * sold_qty * fv / 100
    else:
        sell_sum = sell_price * sold_qty
        profit = (sell_price - asset["avg_price"]) * sold_qty

    asset_currency_id = asset["currency_id"]
    asset_currency_code = get_currency_code(asset_currency_id)
    account_id = asset["broker_id"]

    if account_id is not None:
        cursor.execute(
            "SELECT balance, currency_id FROM accounts WHERE id = ?", (account_id,)
        )
        acc_row = cursor.fetchone()
        if acc_row:
            acc_currency_id = acc_row["currency_id"] or 1
            acc_code = get_currency_code(acc_currency_id)
            rates = _get_rates_from_db(cursor)
            amount_in_acc_currency = compute_amount_in_account_currency(
                sell_sum, asset_currency_code, acc_code, rates
            )
            new_bal = round_price(acc_row["balance"] + amount_in_acc_currency)
            cursor.execute("UPDATE accounts SET balance = ? WHERE id = ?", (new_bal, account_id))

    cursor.execute("UPDATE snapshot_assets SET asset_id = NULL WHERE asset_id = ?", (asset_id,))

    # Сохраняем имя тикера в реестре перед удалением
    upsert_ticker_name(asset["ticker"], asset["name"] or "")

    remaining_qty = asset_quantity - sold_qty
    if remaining_qty <= 0.000001:
        cursor.execute("UPDATE buys SET asset_id = NULL WHERE asset_id = ?", (asset_id,))
        cursor.execute("UPDATE transactions SET asset_id = NULL WHERE asset_id = ?", (asset_id,))
        cursor.execute("DELETE FROM assets WHERE id = ?", (asset_id,))
    else:
        cursor.execute("UPDATE assets SET quantity = quantity - ? WHERE id = ?", (sold_qty, asset_id))

    if account_id is not None:
        acc_code = get_currency_code(acc_currency_id)
        rates = _get_rates_from_db(cursor)
        amount_in_acc_currency = compute_amount_in_account_currency(
            sell_sum, asset_currency_code, acc_code, rates
        )
        sale_asset_id = asset_id if remaining_qty > 0.000001 else None
        add_transaction_internal(cursor, 'продажа', account_id, amount_in_acc_currency,
                                 acc_currency_id, asset["ticker"],
                                 f"Продажа {sold_qty}×{asset['ticker']} по {sell_price:.2f}",
                                 asset_id=sale_asset_id, qty=sold_qty, price=sell_price, profit=profit)

    conn.commit()
    conn.close()

    result = {"ticker": asset["ticker"], "profit": profit, "sold_qty": sold_qty, "remaining_qty": remaining_qty}
    if remaining_qty <= 0.000001:
        msg = f"{asset['ticker']} продан: {sold_qty} шт. Прибыль: {profit:+.2f}"
    else:
        msg = f"{asset['ticker']} продано: {sold_qty} шт., осталось: {remaining_qty:.2f}. Прибыль: {profit:+.2f}"
    return result, True, msg


def buy_more_asset(asset_id, add_qty, buy_price, buy_date, account_id=None):
    """Докупка актива. Если account_id указан — используется он; иначе берётся broker_id актива."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT a.*, c.code AS currency_code
        FROM assets a
        LEFT JOIN currencies c ON a.currency_id = c.id
        WHERE a.id = ?
    """, (asset_id,))
    asset = cursor.fetchone()
    if not asset:
        conn.close()
        return None, False, "Актив не найден"

    effective_account_id = account_id if account_id is not None else asset["broker_id"]
    asset_currency_id = asset["currency_id"]
    asset_currency_code = get_currency_code(asset_currency_id)

    if asset["asset_type"] == "облигация":
        fv = asset["face_value"] or 1000
        purchase_sum = add_qty * buy_price * fv / 100
    else:
        purchase_sum = add_qty * buy_price

    # Регистрируем тикер в справочнике
    upsert_ticker_name(asset["ticker"], asset["name"] or "")

    acc_currency_id = asset_currency_id
    if effective_account_id is not None:
        cursor.execute(
            "SELECT balance, currency_id FROM accounts WHERE id = ?", (effective_account_id,)
        )
        acc_row = cursor.fetchone()
        if acc_row:
            acc_currency_id = acc_row["currency_id"] or 1
            acc_code = get_currency_code(acc_currency_id)
            rates = _get_rates_from_db(cursor)
            amount_in_acc_currency = compute_amount_in_account_currency(
                purchase_sum, asset_currency_code, acc_code, rates
            )
            if acc_row["balance"] < amount_in_acc_currency:
                conn.close()
                return None, False, (
                    f"Недостаточно средств: нужно {amount_in_acc_currency:.2f} {acc_code}, "
                    f"на счёте {acc_row['balance']:.2f} {acc_code}"
                )
            new_bal = round_price(acc_row["balance"] - amount_in_acc_currency)
            cursor.execute("UPDATE accounts SET balance = ? WHERE id = ?", (new_bal, effective_account_id))

    old_qty = asset["quantity"]
    old_avg = asset["avg_price"]
    new_qty = old_qty + add_qty
    new_avg = ((old_avg * old_qty) + (buy_price * add_qty)) / new_qty
    new_avg = round_price(new_avg)

    cursor.execute("UPDATE assets SET quantity=?, avg_price=? WHERE id=?",
                    (new_qty, new_avg, asset_id))

    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("""
        INSERT INTO buys (asset_id, ticker, quantity, price, currency_id, broker_id, buy_date, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (asset_id, asset["ticker"], add_qty, buy_price, asset_currency_id, effective_account_id, buy_date, created_at))

    if effective_account_id is not None:
        acc_code = get_currency_code(acc_currency_id)
        rates = _get_rates_from_db(cursor)
        amount_in_acc_curr = compute_amount_in_account_currency(
            purchase_sum, asset_currency_code, acc_code, rates
        )
        add_transaction_internal(cursor, 'покупка', effective_account_id, amount_in_acc_curr,
                                 acc_currency_id, asset["ticker"],
                                 f"Докупка {add_qty}×{asset['ticker']} по {buy_price:.2f}",
                                 asset_id=asset_id, qty=add_qty, price=buy_price, profit=None)

    conn.commit()
    conn.close()

    result = {"ticker": asset["ticker"], "new_qty": new_qty, "new_avg_price": new_avg,
              "currency": get_currency_code(asset_currency_id)}
    return result, True, f"Докуплено: {add_qty}×{asset['ticker']}"


# ================================================================
#  Пополнение / Списание / Купон-Дивиденд
# ================================================================

def deposit(account_id, amount, notes='', tx_date=None):
    """Пополнение счёта."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT balance, currency_id FROM accounts WHERE id = ?", (account_id,)
    )
    row = cursor.fetchone()
    if not row:
        conn.close()
        return False, "Счёт не найден"
    old_bal = row["balance"]
    cur_id = row["currency_id"]
    currency_code = get_currency_code(cur_id)
    new_bal = round_price(old_bal + amount)
    cursor.execute("UPDATE accounts SET balance = ? WHERE id = ?", (new_bal, account_id))
    add_transaction_internal(cursor, 'пополнение', account_id, amount, cur_id, notes=notes, tx_date=tx_date)
    conn.commit()
    conn.close()
    return True, f"Баланс пополнен на {amount:.2f} {currency_code}. Новый: {new_bal:.2f}"


def withdraw(account_id, amount, notes='', tx_date=None):
    """Списание со счёта."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT balance, currency_id FROM accounts WHERE id = ?", (account_id,)
    )
    row = cursor.fetchone()
    if not row:
        conn.close()
        return False, "Счёт не найден"
    old_bal = row["balance"]
    cur_id = row["currency_id"]
    currency_code = get_currency_code(cur_id)
    new_bal = round_price(old_bal - amount)
    if new_bal < 0:
        conn.close()
        return False, f"Недостаточно средств на счёте (свободно: {old_bal:.2f} {currency_code})"
    cursor.execute("UPDATE accounts SET balance = ? WHERE id = ?", (new_bal, account_id))
    add_transaction_internal(cursor, 'списание', account_id, amount, cur_id, notes=notes, tx_date=tx_date)
    conn.commit()
    conn.close()
    return True, f"Списано {amount:.2f} {currency_code}. Новый баланс: {new_bal:.2f}"


def credit_coupon_or_dividend(account_id, amount, kind='купон', notes='', ticker='', tx_date=None):
    """Зачисление купона или дивиденда."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT balance, currency_id FROM accounts WHERE id = ?", (account_id,)
    )
    row = cursor.fetchone()
    if not row:
        conn.close()
        return False, "Счёт не найден"
    old_bal = row["balance"]
    cur_id = row["currency_id"]
    currency_code = get_currency_code(cur_id)
    new_bal = round_price(old_bal + amount)
    cursor.execute("UPDATE accounts SET balance = ? WHERE id = ?", (new_bal, account_id))

    # Регистрируем тикер в справочнике (если есть имя у актива)
    if ticker:
        cursor.execute("SELECT name FROM assets WHERE ticker = ? AND name IS NOT NULL AND name != '' LIMIT 1", (ticker.strip().upper(),))
        asset_row = cursor.fetchone()
        if asset_row:
            upsert_ticker_name(ticker, asset_row["name"])

    tx_type = kind
    add_transaction_internal(cursor, tx_type, account_id, amount, cur_id, ticker=ticker, notes=notes, tx_date=tx_date)
    conn.commit()
    conn.close()
    return True, f"{kind.capitalize()} начислен: {amount:.2f} {currency_code}. Новый баланс: {new_bal:.2f}"


# ================================================================
#  Portfolio History
# ================================================================

def save_portfolio_history(total_value):
    """Сохранить снимок портфеля."""
    conn = get_connection()
    cursor = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("""
        INSERT INTO portfolio_history (date, total_value, created_at)
        VALUES (?, ?, ?)
    """, (today, total_value, created_at))
    conn.commit()
    conn.close()


def get_portfolio_history():
    """Получить историю портфеля."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM portfolio_history ORDER BY date DESC")
    rows = cursor.fetchall()
    conn.close()
    return rows


# ================================================================
#  Snapshots
# ================================================================

def _fx_rate_for_currency(currency_id, rates):
    """Получить курс валюты в рубли по currency_id."""
    code = get_currency_code(currency_id)
    if code == "USD":
        return rates.get("USD", 90.0)
    elif code == "EUR":
        return rates.get("EUR", 100.0)
    elif code == "CNY":
        return rates.get("CNY", 12.0)
    return 1.0


def save_snapshot():
    """Сохранить срез портфеля за текущую дату для всех активных счетов."""
    conn = get_connection()
    cursor = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    year_month = today[:7]
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rates = _get_rates_from_db(cursor)

    accounts_count = 0
    total_portfolio = 0.0

    cursor.execute("SELECT * FROM accounts WHERE active = 1")
    accounts = cursor.fetchall()

    for acc in accounts:
        aid = acc["id"]
        acc_currency_id = acc["currency_id"]
        acc_currency_code = get_currency_code(acc_currency_id)
        balance = acc["balance"] or 0.0
        balance_rub = balance * _fx_rate_for_currency(acc_currency_id, rates)

        cursor.execute("""
            SELECT a.*, c.code AS currency_code
            FROM assets a
            LEFT JOIN currencies c ON a.currency_id = c.id
            WHERE a.broker_id = ?
        """, (aid,))
        assets = cursor.fetchall()

        assets_value_rub = 0.0
        asset_rows = []
        for a in assets:
            current_price = a["current_price"] or 0
            if current_price <= 0:
                current_price = a["avg_price"]
            currency_id = a["currency_id"]
            value_rub = calculate_total_in_rubles(a["quantity"], current_price,
                                                  currency_id, rates, a["asset_type"],
                                                  a["face_value"] or 1000,
                                                  a["lot_size"] or 1)
            assets_value_rub += value_rub
            fx_rate = _fx_rate_for_currency(currency_id, rates)
            asset_rows.append({
                "asset_id": a["id"],
                "ticker": a["ticker"],
                "name": a["name"] or "",
                "asset_type": a["asset_type"],
                "quantity": a["quantity"],
                "avg_price": a["avg_price"],
                "current_price": current_price,
                "currency_id": currency_id,
                "fx_rate": fx_rate,
                "value_rub": round_price(value_rub),
            })

        portfolio_total = round_price(balance_rub + assets_value_rub)

        _ym = today[:7]
        def sum_by_type(tx_type):
            cursor.execute(
                "SELECT COALESCE(SUM(amount), 0) as s FROM transactions "
                "WHERE account_id = ? AND tx_type = ? AND strftime('%Y-%m', date) = ?",
                (aid, tx_type, _ym)
            )
            return cursor.fetchone()["s"] or 0

        deposits = sum_by_type("пополнение")
        withdrawals = sum_by_type("списание")
        dividends = sum_by_type("дивиденд")
        coupons = sum_by_type("купон")

        cursor.execute(
            "DELETE FROM snapshots WHERE account_id = ? AND strftime('%Y-%m', date) = ?",
            (aid, year_month)
        )

        cursor.execute("""
            INSERT INTO snapshots
                (date, account_id, balance_rub, assets_value_rub, portfolio_total_rub,
                 deposits, withdrawals, dividends, coupons, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (today, aid, round_price(balance_rub), round_price(assets_value_rub),
              portfolio_total, deposits, withdrawals, dividends, coupons, created_at))
        snapshot_id = cursor.lastrowid

        for ar in asset_rows:
            cursor.execute("""
                INSERT INTO snapshot_assets
                    (snapshot_id, asset_id, ticker, name, asset_type,
                     quantity, avg_price, current_price, currency_id, fx_rate, value_rub)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (snapshot_id, ar["asset_id"], ar["ticker"], ar["name"], ar["asset_type"],
                  ar["quantity"], ar["avg_price"], ar["current_price"], ar["currency_id"],
                  ar["fx_rate"], ar["value_rub"]))

        accounts_count += 1
        total_portfolio += portfolio_total

    conn.commit()
    conn.close()
    return accounts_count, round_price(total_portfolio)


def get_snapshots(account_id=None):
    """Получить срезы с именами счетов и суммой портфеля."""
    conn = get_connection()
    cursor = conn.cursor()

    query = """
        SELECT s.id, s.date, s.account_id, a.name as account_name,
               a.account_number,
               s.balance_rub, s.assets_value_rub, s.portfolio_total_rub,
               s.deposits, s.withdrawals, s.dividends, s.coupons,
               s.created_at
        FROM snapshots s
        LEFT JOIN accounts a ON s.account_id = a.id
        WHERE 1=1
    """
    params = []
    if account_id:
        query += " AND s.account_id = ?"
        params.append(account_id)
    query += " ORDER BY s.date DESC, s.account_id"

    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()
    return rows


def get_snapshot_assets(snapshot_id):
    """Получить детали среза (активы)."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT snapshot_id, asset_id, ticker, name, asset_type,
               quantity, avg_price, current_price,
               currency_id, fx_rate, value_rub
        FROM snapshot_assets
        WHERE snapshot_id = ?
        ORDER BY ticker
    """, (snapshot_id,))
    rows = cursor.fetchall()
    conn.close()
    return rows


# ================================================================
#  Импорт исторических срезов
# ================================================================

_MONTH_NAMES = [
    'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
    'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь',
]


def import_asset_slices(broker_id, year, asset_rows, balance_row=None, deposit_row=None):
    """
    Импорт исторических срезов стоимости активов.

    broker_id: int — ID счёта/брокера
    year: int — год срезов
    asset_rows: list of dict с ключами:
        name, ticker, asset_type, currency_code,
        month_values: dict {1..12: float}
    balance_row: dict с ключами:
        currency_code, month_values: dict {1..12: float}
        или None
    deposit_row: dict с ключами:
        currency_code, month_values: dict {1..12: float}
        или None  (положительные значения -> deposits, отрицательные -> withdrawals)

    Возвращает:
        (-1, список_месяцев) — если за некоторые месяцы уже есть срезы
        (кол-во_месяцев, кол-во_срезов) — при успехе
    """
    conn = get_connection()
    cursor = conn.cursor()

    # Собрать все целевые месяцы (активы + баланс)
    target_months = set()
    for row in asset_rows:
        target_months.update(row['month_values'].keys())
    if balance_row and balance_row.get('month_values'):
        target_months.update(balance_row['month_values'].keys())
    if deposit_row and deposit_row.get('month_values'):
        target_months.update(deposit_row['month_values'].keys())

    if not target_months:
        conn.close()
        return (0, 0)

    # Проверить существующие срезы для broker_id + год + целевые месяцы
    placeholders = ','.join(['?' for _ in target_months])
    year_str = str(year)
    months_list = sorted(target_months)
    params = [broker_id, year_str] + months_list
    cursor.execute(f"""
        SELECT DISTINCT CAST(strftime('%m', date) AS INTEGER) as m
        FROM snapshots
        WHERE account_id = ? AND strftime('%Y', date) = ?
          AND CAST(strftime('%m', date) AS INTEGER) IN ({placeholders})
    """, params)

    existing = cursor.fetchall()
    if existing:
        conflict_months = [_MONTH_NAMES[int(r['m']) - 1] for r in existing]
        conn.close()
        return (-1, conflict_months)

    # Импорт
    rates = _get_rates_from_db(cursor)
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    months_created = 0
    slices_created = 0

    for month_num in sorted(target_months):
        # Собрать данные для этого месяца
        month_data = []
        for row in asset_rows:
            if month_num in row['month_values']:
                month_data.append(row)

        # Баланс для этого месяца
        balance_val = 0.0
        if balance_row and month_num in balance_row['month_values']:
            balance_val = balance_row['month_values'][month_num]

        # Пополнения/вывод для этого месяца
        deposits_val = 0.0
        withdrawals_val = 0.0
        if deposit_row and month_num in deposit_row['month_values']:
            raw = deposit_row['month_values'][month_num]
            if raw > 0:
                deposits_val = raw
            elif raw < 0:
                withdrawals_val = abs(raw)

        # Пропускать только если нет ни активов, ни баланса, ни движений
        if not month_data and not balance_val and not deposits_val and not withdrawals_val:
            continue

        # Последний день месяца
        last_day = calendar.monthrange(year, month_num)[1]
        date_str = f"{year:04d}-{month_num:02d}-{last_day:02d}"

        # Баланс в рублях
        if balance_val != 0:
            bcid = get_currency_id(balance_row['currency_code'])
            bfx = _fx_rate_for_currency(bcid, rates)
            balance_rub = round_price(balance_val * bfx)
        else:
            balance_rub = 0.0

        # Пополнения/выводы в рублях
        if deposits_val != 0:
            dcid = get_currency_id(deposit_row['currency_code'])
            dfx = _fx_rate_for_currency(dcid, rates)
            deposits_rub = round_price(deposits_val * dfx)
        else:
            deposits_rub = 0.0
        if withdrawals_val != 0:
            dcid2 = get_currency_id(deposit_row['currency_code'])
            dfx2 = _fx_rate_for_currency(dcid2, rates)
            withdrawals_rub = round_price(withdrawals_val * dfx2)
        else:
            withdrawals_rub = 0.0

        # Сумма активов в рублях
        assets_value_rub = 0.0
        for row in month_data:
            val = row['month_values'][month_num]
            cid = get_currency_id(row['currency_code'])
            fx = _fx_rate_for_currency(cid, rates)
            assets_value_rub += round_price(val * fx)

        assets_value_rub = round_price(assets_value_rub)
        portfolio_total = round_price(balance_rub + assets_value_rub)

        # Удалить старую запись (защита)
        year_month = f"{year:04d}-{month_num:02d}"
        cursor.execute("""
            DELETE FROM snapshots
            WHERE account_id = ? AND strftime('%Y-%m', date) = ?
        """, (broker_id, year_month))

        # Вставить срез
        cursor.execute("""
            INSERT INTO snapshots
                (date, account_id, balance_rub, assets_value_rub, portfolio_total_rub,
                 deposits, withdrawals, dividends, coupons, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, ?)
        """, (date_str, broker_id, balance_rub, assets_value_rub, portfolio_total,
              deposits_rub, withdrawals_rub, created_at))
        snapshot_id = cursor.lastrowid

        # Вставить активы в срез (баланс не попадает в snapshot_assets)
        for row in month_data:
            val = row['month_values'][month_num]
            cid = get_currency_id(row['currency_code'])
            fx = _fx_rate_for_currency(cid, rates)
            value_rub = round_price(val * fx)

            cursor.execute("""
                INSERT INTO snapshot_assets
                    (snapshot_id, asset_id, ticker, name, asset_type,
                     quantity, avg_price, current_price, currency_id, fx_rate, value_rub)
                VALUES (?, NULL, ?, ?, ?, 1, 0, ?, ?, ?, ?)
            """, (snapshot_id, row['ticker'], row['name'], row['asset_type'],
                  val, cid, fx, value_rub))
            slices_created += 1

        months_created += 1

    conn.commit()
    conn.close()
    return (months_created, slices_created)


# ================================================================
#  Валютные курсы
# ================================================================

def get_exchange_rates():
    """Получить курсы валют из настроек."""
    conn = get_connection()
    cursor = conn.cursor()
    rates = {"RUB": 1.0, "USD": 90.0, "EUR": 100.0, "CNY": 12.0}
    cursor.execute("SELECT setting_value FROM settings WHERE setting_key = 'usd_rub_rate'")
    row = cursor.fetchone()
    if row:
        try:
            rates["USD"] = float(row["setting_value"])
        except (ValueError, TypeError):
            pass
    cursor.execute("SELECT setting_value FROM settings WHERE setting_key = 'eur_rub_rate'")
    row = cursor.fetchone()
    if row:
        try:
            rates["EUR"] = float(row["setting_value"])
        except (ValueError, TypeError):
            pass
    cursor.execute("SELECT setting_value FROM settings WHERE setting_key = 'cny_rub_rate'")
    row = cursor.fetchone()
    if row:
        try:
            rates["CNY"] = float(row["setting_value"])
        except (ValueError, TypeError):
            pass
    conn.close()
    return rates


def calculate_total_in_rubles(quantity, avg_price, currency_id, rates, asset_type="акция", face_value=1000, lot_size=1):
    """Рассчитать стоимость в рублях."""
    code = get_currency_code(currency_id)
    if asset_type == "облигация":
        total = quantity * avg_price * face_value / 100
    else:
        lot = lot_size if lot_size and lot_size > 0 else 1
        total = quantity * lot * avg_price
    if code == "USD":
        total *= rates.get("USD", 90.0)
    elif code == "EUR":
        total *= rates.get("EUR", 100.0)
    elif code == "CNY":
        total *= rates.get("CNY", 12.0)
    return total


# ================================================================
#  Prices
# ================================================================

def update_asset_price(asset_id, current_price, last_update, face_value=None, lot_size=None, lot_value=None, list_level=None, coupon_percent=None):
    """Обновить текущую цену, дату и (опционально) метаданные облигации."""
    conn = get_connection()
    cursor = conn.cursor()
    sets = []
    params = []
    sets.append("current_price = ?")
    params.append(current_price)
    sets.append("last_update = ?")
    params.append(last_update)
    if face_value is not None:
        sets.append("face_value = ?")
        params.append(face_value)
    if lot_size is not None:
        sets.append("lot_size = ?")
        params.append(lot_size)
    if lot_value is not None:
        sets.append("lot_value = ?")
        params.append(lot_value)
    if list_level is not None:
        sets.append("list_level = ?")
        params.append(list_level)
    if coupon_percent is not None:
        sets.append("coupon_percent = ?")
        params.append(coupon_percent)
    params.append(asset_id)
    cursor.execute(f"""
        UPDATE assets
        SET {', '.join(sets)}
        WHERE id = ?
    """, tuple(params))
    conn.commit()
    conn.close()


# ================================================================
#  Analysis helpers
# ================================================================

def get_active_accounts():
    """Получить только активные счёта."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM accounts WHERE active = 1 ORDER BY name")
    rows = cursor.fetchall()
    conn.close()
    return rows


def get_snapshot_years():
    """Возвратить список уникальных лет из snapshots + текущий год, по убыванию."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT strftime('%Y', date) as y FROM snapshots ORDER BY y DESC")
    rows = [r["y"] for r in cursor.fetchall()]
    current = str(datetime.now().year)
    if current not in rows:
        rows.insert(0, current)
    conn.close()
    return rows


def get_analysis_monthly(year_from, year_to, account_id_or_all='all'):
    """Получить данные по срезам за период из snapshots."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT strftime('%Y-%m', date) as ym,
               SUM(balance_rub) as balance_rub,
               SUM(assets_value_rub) as assets_value_rub,
               SUM(portfolio_total_rub) as portfolio_total_rub,
               SUM(deposits) as deposits,
               SUM(withdrawals) as withdrawals
        FROM snapshots
        WHERE strftime('%Y', date) BETWEEN ? AND ?
          AND (? = 'all' OR account_id = ?)
        GROUP BY ym
        ORDER BY ym ASC
    """, (year_from, year_to, account_id_or_all, account_id_or_all))
    rows = cursor.fetchall()

    prev_ym = f"{int(year_from) - 1}-12"
    cursor.execute("""
        SELECT SUM(portfolio_total_rub) as prev_total
        FROM snapshots
        WHERE (? = 'all' OR account_id = ?)
          AND strftime('%Y-%m', date) = ?
    """, (account_id_or_all, account_id_or_all, prev_ym))
    row = cursor.fetchone()
    prev_total = row["prev_total"] if row and row["prev_total"] else None
    conn.close()

    return rows, prev_total


# ================================================================
#  Доходы — купоны и дивиденды
# ================================================================

_INCOME_TYPE_MAP = {'Все доходы': None, 'Купоны': 'купон', 'Дивиденды': 'дивиденд'}


def get_income_transactions(year_from, year_to, account_id_or_all='all', type_or_all='Все доходы'):
    """Получить транзакции купонов/дивидендов за период."""
    conn = get_connection()
    cursor = conn.cursor()

    where = "WHERE t.tx_type IN ('купон', 'дивиденд') AND strftime('%Y', t.date) BETWEEN ? AND ?"
    params = [year_from, year_to]
    if account_id_or_all != 'all':
        where += " AND t.account_id = ?"
        params.append(account_id_or_all)
    db_type = _INCOME_TYPE_MAP.get(type_or_all)
    if db_type:
        where += " AND t.tx_type = ?"
        params.append(db_type)

    query = f"""
        SELECT strftime('%Y-%m', t.date) as month,
               t.ticker,
               COALESCE(tn.name, t.ticker) as asset_name,
               t.tx_type,
               t.amount,
               c.code AS currency_code,
               COALESCE(acc.name, '') as account_name,
               t.date
         FROM transactions t
         LEFT JOIN currencies c ON t.currency_id = c.id
         LEFT JOIN ticker_names tn ON t.ticker = tn.ticker
         LEFT JOIN accounts acc ON t.account_id = acc.id
         {where}
         ORDER BY t.date DESC
     """
    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()
    return rows


def get_income_monthly_totals(year_from, year_to, account_id_or_all='all', type_or_all='Все доходы'):
    """Получить агрегаты доходов по месяцам для графика за период."""
    conn = get_connection()
    cursor = conn.cursor()

    where = "WHERE t.tx_type IN ('купон', 'дивиденд') AND strftime('%Y', t.date) BETWEEN ? AND ?"
    params = [year_from, year_to]
    if account_id_or_all != 'all':
        where += " AND t.account_id = ?"
        params.append(account_id_or_all)
    db_type = _INCOME_TYPE_MAP.get(type_or_all)
    if db_type:
        where += " AND t.tx_type = ?"
        params.append(db_type)

    query = f"""
        SELECT strftime('%Y-%m', t.date) as month,
               t.tx_type,
               SUM(t.amount) as total_amount,
               c.code AS currency_code
        FROM transactions t
        LEFT JOIN currencies c ON t.currency_id = c.id
        {where}
        GROUP BY month, tx_type
        ORDER BY month ASC
    """
    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()
    return rows


# ================================================================
#  Импорт доходов (купоны, дивиденды) из CSV
# ================================================================

_INCOME_MONTH_NAMES = [
    'январь', 'февраль', 'март', 'апрель', 'май', 'июнь',
    'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь',
]


def import_incomes(account_id, year, income_rows):
    """
    Импорт купонов/дивидендов из CSV. Только транзакции, без изменения баланса.

    account_id: int — ID счёта/брокера
    year: int — год
    income_rows: list of dict с ключами:
        name, ticker, income_type (купон/дивиденд), currency_code,
        month_values: dict {1..12: float}

    Возвращает: (кол-во_записей, кол-во_без_актива)
    """
    conn = get_connection()
    cursor = conn.cursor()

    # Кэш asset_id по тикеру для данного брокера
    cursor.execute("""
        SELECT id, ticker FROM assets WHERE broker_id = ?
    """, (account_id,))
    ticker_to_id = {row['ticker'].strip(): row['id'] for row in cursor.fetchall()}

    created = 0
    without_asset = 0

    for row in income_rows:
        ticker = row['ticker']
        income_type = row['income_type']
        currency_code = row['currency_code']
        month_values = row['month_values']
        name = row.get('name', '')

        # Сохраняем тикер+имя в реестр
        if ticker:
            upsert_ticker_name(ticker, name)

        currency_id = get_currency_id(currency_code)
        asset_id = ticker_to_id.get(ticker.strip())

        if asset_id is None:
            without_asset += 1

        for month_num in sorted(month_values.keys()):
            amount = month_values[month_num]
            if amount <= 0:
                continue

            last_day = calendar.monthrange(year, month_num)[1]
            date_str = f"{year:04d}-{month_num:02d}-{last_day:02d}"
            notes = f"Импорт за {year} {_INCOME_MONTH_NAMES[month_num - 1]}"

            add_transaction_internal(cursor, income_type, account_id, amount, currency_id, ticker,
                                     notes, tx_date=date_str, asset_id=asset_id)
            created += 1

    conn.commit()
    conn.close()
    return (created, without_asset)


# ================================================================
#  Сделки — покупки и продажи
# ================================================================

def get_all_tickers():
    """Получить уникальные тикеры из assets."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT ticker FROM assets WHERE ticker != '' ORDER BY ticker")
    rows = [r["ticker"] for r in cursor.fetchall()]
    conn.close()
    return rows


_DEAL_TYPE_MAP = {'Все': None, 'Покупки': 'покупка', 'Продажи': 'продажа'}


def get_deal_transactions(year_from, year_to, account_id_or_all='all', type_or_all='Все', ticker_or_all='Все'):
    """Получить сделки (покупки/продажи) за период."""
    conn = get_connection()
    cursor = conn.cursor()

    where = "WHERE t.tx_type IN ('покупка', 'продажа') AND strftime('%Y', t.date) BETWEEN ? AND ?"
    params = [year_from, year_to]
    if account_id_or_all != 'all':
        where += " AND t.account_id = ?"
        params.append(account_id_or_all)
    db_type = _DEAL_TYPE_MAP.get(type_or_all)
    if db_type:
        where += " AND t.tx_type = ?"
        params.append(db_type)
    if ticker_or_all != 'Все':
        where += " AND t.ticker = ?"
        params.append(ticker_or_all)

    query = f"""
        SELECT t.date,
               t.ticker,
               COALESCE(tn.name, t.ticker) as asset_name,
               t.tx_type,
               t.qty,
               t.price,
               t.qty * t.price as amount,
               COALESCE(c.code, 'RUB') as currency_code,
               COALESCE(acc.name, '') as account_name,
               t.profit,
               t.amount as amount_rub
         FROM transactions t
         LEFT JOIN currencies c ON t.currency_id = c.id
         LEFT JOIN ticker_names tn ON t.ticker = tn.ticker
         LEFT JOIN accounts acc ON t.account_id = acc.id
         {where}
         ORDER BY t.date DESC
     """
    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()
    return rows

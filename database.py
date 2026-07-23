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
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError:
        pass
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

def upsert_ticker_name(ticker, name, cursor=None):
    """Добавить или обновить запись в реестре тикеров.

    Если передан cursor — работает в рамках транзакции вызывающего
    (без открытия нового соединения и без commit/close). Иначе открывает
    собственное подключение.
    """
    if not ticker or not name:
        return
    own_conn = cursor is None
    if own_conn:
        conn = get_connection()
        cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO ticker_names (ticker, name) VALUES (?, ?)
        ON CONFLICT(ticker) DO UPDATE SET name = ?
        WHERE excluded.name != '' AND ticker_names.name = ''
    """, (ticker.strip().upper(), name.strip(), name.strip()))
    if own_conn:
        conn.commit()
        conn.close()


def _ticker_set_clause(extra_updates):
    """Собрать строку SET для INSERT ... ON CONFLICT DO UPDATE с учётом старых и новых полей."""
    clauses = [f"excluded.{k} = ticker_names.{k}" for k in extra_updates]
    return ", ".join(clauses)


def import_ticker_names(rows):
    """Массовый импорт/обновление записей тикеров.

    rows: список кортежей произвольной длины —
      [(ticker,), (ticker, name), (ticker, name, type), (ticker, name, type, lot), (ticker, name, type, lot, currency), ...]
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        for row in rows:
            ticker = (row[0] or '').strip().upper()
            if not ticker:
                continue
            name = (row[1] or '').strip() if len(row) > 1 else ''
            asset_type = (row[2] or '').strip() if len(row) > 2 else ''
            lot_size = row[3] if len(row) > 3 else 1
            try:
                lot_size = int(lot_size) if lot_size else 1
            except (ValueError, TypeError):
                lot_size = 1
            currency = (row[4] or '').strip() if len(row) > 4 else ''

            # Формируем SET-кlausулы только для явно переданных колонок
            set_clauses = []
            if len(row) > 1 and name:
                set_clauses.append("name = excluded.name")
            if len(row) > 2 and asset_type:
                set_clauses.append("asset_type = excluded.asset_type")
            if len(row) > 3 and lot_size > 0:
                set_clauses.append("lot_size = excluded.lot_size")
            if len(row) > 4 and currency:
                set_clauses.append("currency = excluded.currency")
            set_str = ", ".join(set_clauses) or "name = excluded.name"

            cursor.execute(f"""
                INSERT INTO ticker_names (ticker, name, asset_type, lot_size, currency)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(ticker) DO UPDATE SET {set_str}
            """, (ticker, name, asset_type, lot_size, currency))
        count = len([r for r in rows if (r[0] or '').strip()])
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()
    return count


def get_all_ticker_names():
    """Получить все записи реестра: [(ticker, name, asset_type, lot_size, currency), ...]."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT ticker, name, asset_type, lot_size, currency FROM ticker_names ORDER BY ticker")
    rows = cursor.fetchall()
    conn.close()
    return rows


def search_ticker_names(query, limit=3):
    """Поиск тикеров по подстроке (case-insensitive, кириллица через Python .lower()).
    Ищет по ticker и name. Сортировка: точное совпадение → starts-with ticker → starts-with name → contains.
    Возвращает [(ticker, name, asset_type, lot_size, currency), ...] до limit шт.
    """
    q = (query or '').strip().lower()
    if not q:
        return []
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT ticker, name, asset_type, lot_size, currency FROM ticker_names")
    rows = cursor.fetchall()
    conn.close()

    matches = []
    for row in rows:
        ticker = row[0]
        name = row[1] or ''
        ticker_lower = ticker.lower()
        name_lower = name.lower()
        if q not in ticker_lower and q not in name_lower:
            continue
        if ticker_lower == q:
            priority = 0
        elif ticker_lower.startswith(q):
            priority = 1
        elif name_lower.startswith(q):
            priority = 2
        else:
            priority = 3
        matches.append((priority, ticker_lower, row))

    matches.sort(key=lambda x: (x[0], x[1]))
    return [m[2] for m in matches[:limit]]


def update_ticker_name(ticker, name, lot_size=None, currency=None, asset_type=None):
    """Обновить имя тикера (и опционально lot_size, currency, asset_type)."""
    conn = get_connection()
    cursor = conn.cursor()
    ticker = str(ticker).strip().upper()
    name = str(name).strip() if name else ''
    sets = ["name = ?"]
    params = [name]
    if lot_size is not None:
        sets.append("lot_size = ?")
        try:
            params.append(int(lot_size))
        except (ValueError, TypeError):
            params.append(1)
    if currency is not None:
        sets.append("currency = ?")
        params.append(str(currency).strip().upper() or '')
    if asset_type is not None:
        sets.append("asset_type = ?")
        params.append(str(asset_type).strip() if asset_type else '')
    params.append(ticker)
    cursor.execute(f"UPDATE ticker_names SET {', '.join(sets)} WHERE ticker = ?", params)
    conn.commit()
    conn.close()


def add_ticker_name(ticker, name, asset_type='', lot_size=1, currency=''):
    """Добавить новую запись в реестр тикеров."""
    conn = get_connection()
    cursor = conn.cursor()
    ticker = str(ticker).strip().upper()
    name = str(name).strip() if name else ''
    asset_type = str(asset_type).strip() if asset_type else ''
    try:
        lot_size = int(lot_size) if lot_size else 1
    except (ValueError, TypeError):
        lot_size = 1
    currency = str(currency).strip().upper() if currency else ''
    try:
        cursor.execute(
            "INSERT INTO ticker_names (ticker, name, asset_type, lot_size, currency) VALUES (?, ?, ?, ?, ?)",
            (ticker, name, asset_type, lot_size, currency)
        )
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
    """Переименовать тикер с каскадным обновлением всех связанных таблиц.

    Поля asset_type, lot_size, currency переносятся как есть.
    """
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
            cursor.execute(
                "UPDATE ticker_names SET ticker = ?, name = ? WHERE ticker = ?",
                (new_ticker, name, old_ticker)
            )
        else:
            cursor.execute("UPDATE ticker_names SET name = ? WHERE ticker = ?", (name, old_ticker))
    except Exception as e:
        conn.rollback()
        raise e
    conn.commit()
    conn.close()


def convert_placeholder_tickers():
    """Сопоставить технические тикеры АКТИВ_* с настоящими тикерами из реестра.

    После импорта исторических срезов из CSV строки без тикера получают условное
    обозначение (АКТИВ_1, АКТИВ_2 …). Счётчик обнуляется при каждом импорте,
    поэтому один и тот же АКТИВ_N в срезах разных лет может соответствовать
    РАЗНЫМ реальным активам. Поэтому сопоставление выполняется ПОСТРОЧНО:
    каждая строка таблиц берёт собственное name и ищет тикер в реестре.

    При однозначном совпадении имени обновляется только эта строка
    (через rowid для snapshot_assets и через id для assets).

    Таблицы buys и transactions не затрагиваются: в них нет колонки name и
    технические тикеры туда при импорте не попадают.

    Returns:
        dict с ключами:
            converted: list[(old_ticker, new_ticker, name)] — успешные замены
                       (уникальные тройки, без дублей по строкам)
            ambiguous: list[(old_ticker, name, [tickers...])] — несколько кандидатов
            not_found: list[(old_ticker, name)] — нет совпадения по имени
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        # 1. Реестр тикеров: нормализованное имя -> [тикеры]
        cursor.execute("SELECT ticker, name FROM ticker_names")
        name_map = {}
        for row in cursor.fetchall():
            name = (row["name"] or "").strip()
            if name:
                norm = " ".join(name.casefold().split())
                name_map.setdefault(norm, []).append(row["ticker"])

        converted = []
        ambiguous = []
        not_found = []
        seen_conv = set()    # для дедупликации отчёта
        seen_amb = set()
        seen_nf = set()

        # 2. snapshot_assets — построчно через rowid
        cursor.execute(
            "SELECT rowid, ticker, name FROM snapshot_assets "
            "WHERE ticker LIKE 'АКТИВ\\_%' ESCAPE '\\'"
        )
        for row in cursor.fetchall():
            old_ticker = row["ticker"]
            name = (row["name"] or "").strip()
            if not name:
                key = (old_ticker, "")
                if key not in seen_nf:
                    seen_nf.add(key)
                    not_found.append((old_ticker, ""))
                continue
            norm = " ".join(name.casefold().split())
            candidates = name_map.get(norm)
            if not candidates:
                key = (old_ticker, name)
                if key not in seen_nf:
                    seen_nf.add(key)
                    not_found.append((old_ticker, name))
            elif len(candidates) == 1:
                new_ticker = candidates[0]
                cursor.execute(
                    "UPDATE snapshot_assets SET ticker = ? WHERE rowid = ?",
                    (new_ticker, row["rowid"])
                )
                key = (old_ticker, new_ticker, name)
                if key not in seen_conv:
                    seen_conv.add(key)
                    converted.append((old_ticker, new_ticker, name))
            else:
                key = (old_ticker, name)
                if key not in seen_amb:
                    seen_amb.add(key)
                    ambiguous.append((old_ticker, name, candidates))

        # 3. assets — построчно через id (на случай ручного ввода)
        cursor.execute(
            "SELECT id, ticker, name FROM assets "
            "WHERE ticker LIKE 'АКТИВ\\_%' ESCAPE '\\'"
        )
        for row in cursor.fetchall():
            name = (row["name"] or "").strip()
            if not name:
                continue
            norm = " ".join(name.casefold().split())
            candidates = name_map.get(norm)
            if len(candidates) == 1:
                cursor.execute(
                    "UPDATE assets SET ticker = ? WHERE id = ?",
                    (candidates[0], row["id"])
                )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {"converted": converted, "ambiguous": ambiguous, "not_found": not_found}


def get_ticker_name(ticker):
    """Получить имя тикера из реестра."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM ticker_names WHERE ticker = ?", (str(ticker).strip().upper(),))
    row = cursor.fetchone()
    conn.close()
    return row["name"] if row else None


def get_ticker_info(ticker):
    """Получить полную информацию о тикере из реестра.

    Returns:
        dict или None: {ticker, name, asset_type, lot_size, currency}
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT ticker, name, asset_type, lot_size, currency FROM ticker_names WHERE ticker = ?",
                    (str(ticker).strip().upper(),))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {
            "ticker": row["ticker"],
            "name": row["name"] or '',
            "asset_type": row["asset_type"] or '',
            "lot_size": row["lot_size"] if row["lot_size"] else 1,
            "currency": row["currency"] or '',
        }
    return None


def update_ticker_from_moex(ticker, shortname, currency, lot_size, asset_type=None):
    """Обновить запись тикера данными из Мосбиржи.

    Args:
        ticker: тикер (верхний регистр)
        shortname: SHORTNAME с биржи (перезапишет name если отличается)
        currency: валюта (SUR→RUB уже должно быть сделано вызывающим)
        lot_size: лотность (int)
        asset_type: тип ('акция'/'облигация'/'etf') — None = не трогать
    """
    conn = get_connection()
    cursor = conn.cursor()
    ticker = str(ticker).strip().upper()
    shortname = str(shortname).strip() if shortname else ''
    currency = str(currency).strip().upper() if currency else ''
    try:
        lot_size = int(lot_size) if lot_size else 1
    except (ValueError, TypeError):
        lot_size = 1

    # Проверим, существует ли тикер
    cursor.execute("SELECT name, asset_type, lot_size, currency FROM ticker_names WHERE ticker = ?", (ticker,))
    existing = cursor.fetchone()
    if not existing:
        # Создаём новую запись
        sets = ["name = ?", "lot_size = ?", "currency = ?"]
        params = [shortname, lot_size, currency]
        if asset_type:
            sets.append("asset_type = ?")
            params.append(asset_type)
        params.append(ticker)
        cursor.execute(f"INSERT INTO ticker_names (ticker, name, asset_type, lot_size, currency) VALUES (?, ?, ?, ?, ?)",
                        (ticker, shortname, asset_type or '', lot_size, currency))
    else:
        sets = []
        params = []
        # name обновляем если отличается
        if shortname and shortname != existing["name"]:
            sets.append("name = ?")
            params.append(shortname)
        # asset_type обновляем только если был пустой
        if asset_type and not existing["asset_type"]:
            sets.append("asset_type = ?")
            params.append(asset_type)
        # lot_size всегда обновляем если пришло значение
        if lot_size > 0 and lot_size != existing["lot_size"]:
            sets.append("lot_size = ?")
            params.append(lot_size)
        # currency обновляем если есть значение
        if currency and currency != existing["currency"]:
            sets.append("currency = ?")
            params.append(currency)
        if sets:
            params.append(ticker)
            cursor.execute(f"UPDATE ticker_names SET {', '.join(sets)} WHERE ticker = ?", params)
    conn.commit()
    conn.close()


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
              name='', currency_code='RUB', lot_size=1):
    """Добавление актива."""
    currency_id = get_currency_id(currency_code)
    conn = get_connection()
    cursor = conn.cursor()
    price = round_price(price)
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ls = lot_size if lot_size and lot_size > 0 else 1

    # Регистрируем тикер в справочнике
    upsert_ticker_name(ticker, name, cursor=cursor)

    if asset_type == "облигация":
        purchase_sum = quantity * price * 1000 / 100
    else:
        purchase_sum = quantity * ls * price

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
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1000, ?, 1000, NULL)
    """, (ticker, name, asset_type, quantity, price, account_id, purchase_date, created_at, currency_id, ls))
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
    upsert_ticker_name(ticker, name, cursor=cursor)
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

    ls = asset["lot_size"] if asset["lot_size"] and asset["lot_size"] > 0 else 1
    if asset["asset_type"] == "облигация":
        fv = asset["face_value"] or 1000
        sell_sum = sell_price * sold_qty * fv / 100
        profit = (sell_price - asset["avg_price"]) * sold_qty * fv / 100
    else:
        sell_sum = sell_price * sold_qty * ls
        profit = (sell_price - asset["avg_price"]) * sold_qty * ls

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
    upsert_ticker_name(asset["ticker"], asset["name"] or "", cursor=cursor)

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


def buy_more_asset(asset_id, add_qty, buy_price, buy_date, account_id=None, lot_size=None):
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
    ls = asset["lot_size"] if asset["lot_size"] and asset["lot_size"] > 0 else (lot_size or 1)

    if asset["asset_type"] == "облигация":
        fv = asset["face_value"] or 1000
        purchase_sum = add_qty * buy_price * fv / 100
    else:
        purchase_sum = add_qty * ls * buy_price

    # Регистрируем тикер в справочнике
    upsert_ticker_name(asset["ticker"], asset["name"] or "", cursor=cursor)

    # Обновляем lot_size актива, если пришёл новый из формы
    if lot_size is not None and ls == lot_size and asset["lot_size"] != lot_size:
        cursor.execute("UPDATE assets SET lot_size = ? WHERE id = ?", (lot_size, asset_id))

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
    try:
        cursor.execute(
            "SELECT balance, currency_id FROM accounts WHERE id = ?", (account_id,)
        )
        row = cursor.fetchone()
        if not row:
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
                upsert_ticker_name(ticker, asset_row["name"], cursor=cursor)

        tx_type = kind
        add_transaction_internal(cursor, tx_type, account_id, amount, cur_id, ticker=ticker, notes=notes, tx_date=tx_date)
        conn.commit()
        return True, f"{kind.capitalize()} начислен: {amount:.2f} {currency_code}. Новый баланс: {new_bal:.2f}"
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


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


def get_latest_snapshot_month():
    """Вернуть последний год-месяц ('YYYY-MM') среза или None, если срезов нет."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT MAX(strftime('%Y-%m', date)) as ym FROM snapshots")
    row = cursor.fetchone()
    conn.close()
    return row["ym"] if row and row["ym"] else None


def save_snapshot(target_ym=None):
    """Сохранить срез портфеля за текущую дату для всех активных счетов.

    Args:
        target_ym: str "YYYY-MM" — целевой месяц для среза (бэкап за
                   пропущенный месяц). Дата среза = последний день
                   target_ym, транзакции суммируются за target_ym.
                   Если None — используется текущий месяц.
    """
    conn = get_connection()
    cursor = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    year_month = target_ym if target_ym else today[:7]
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if target_ym:
        try:
            yr, mo = int(target_ym[:4]), int(target_ym[5:7])
        except ValueError:
            yr, mo = datetime.now().year, datetime.now().month
        last_day = calendar.monthrange(yr, mo)[1]
        snapshot_date = f"{target_ym}-{last_day:02d}"
    else:
        snapshot_date = today

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

        _ym = year_month
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
        """, (snapshot_date, aid, round_price(balance_rub), round_price(assets_value_rub),
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


def get_prev_month_asset_values(account_id=None):
    """Вернуть прошлые стоимости активов для самого свежего завершённого
    месячного среза (строго раньше текущего месяца).

    Args:
        account_id: фильтр по счёту; None — по всем счетам.

    Returns:
        dict {(account_id, ticker): (value_rub, quantity)} — сумма value_rub и
        сумма quantity по паре «счёт + тикер» за последний прошедший месяц.
        Количество нужно для сравнения цены за единицу (а не общей стоимости),
        чтобы изменение числа бумаг не искажало результат. Пустой словарь, если
        прошлого среза нет.
    """
    conn = get_connection()
    cursor = conn.cursor()

    if account_id:
        cursor.execute("""
            SELECT MAX(strftime('%Y-%m', date)) AS ym
            FROM snapshots
            WHERE account_id = ?
              AND strftime('%Y-%m', date) < strftime('%Y-%m', 'now')
        """, (account_id,))
    else:
        cursor.execute("""
            SELECT MAX(strftime('%Y-%m', date)) AS ym
            FROM snapshots
            WHERE strftime('%Y-%m', date) < strftime('%Y-%m', 'now')
        """)
    row = cursor.fetchone()
    ym = row["ym"] if row and row["ym"] else None
    if not ym:
        conn.close()
        return {}

    cursor.execute("""
        SELECT s.account_id AS aid, sa.ticker AS ticker,
               SUM(sa.value_rub) AS v, SUM(sa.quantity) AS q
        FROM snapshot_assets sa
        JOIN snapshots s ON sa.snapshot_id = s.id
        WHERE strftime('%Y-%m', s.date) = ?
          AND (? IS NULL OR s.account_id = ?)
        GROUP BY s.account_id, sa.ticker
    """, (ym, account_id, account_id))

    result = {(r["aid"], r["ticker"]): (r["v"], r["q"]) for r in cursor.fetchall()}
    conn.close()
    return result


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

    try:
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
                upsert_ticker_name(ticker, name, cursor=cursor)

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
        return (created, without_asset)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


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

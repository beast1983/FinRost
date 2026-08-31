"""Импорт заявок QUIK из XLSX-экспорта.

XLSX читается стандартной библиотекой (zipfile + ElementTree), без pandas
и openpyxl. Исполненные заявки записываются в transactions; частично
исполненные (снятые с «Исполнено» > 0) импортируются по фактически
исполненному количеству с пропорциональной суммой. В режиме
update_portfolio дополнительно создаются/обновляются активы и меняется
баланс счёта (как при обычной покупке/продаже в приложении).

Ключ дедупликации — номер заявки, хранится в notes в формате:
    QUIK №83165064679 11:03:55
"""

import re
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime

import tkinter as tk
import ttkbootstrap as tb
from tkinter import messagebox

from calendar_utils import create_date_entry
from database import (
    get_connection, add_transaction_internal, get_currency_id,
    get_currency_code, compute_amount_in_account_currency,
    _get_rates_from_db, round_price, _write_lock,
)

_NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'

# Коды валют QUIK → коды приложения
CURRENCY_MAP = {
    'SUR': 'RUB', 'RUB': 'RUB',
    'USD': 'USD', 'USDRUB_TOM': 'USD',
    'EUR': 'EUR', 'EURRUB_TOM': 'EUR',
    'CNY': 'CNY', 'CNYRUB_TOM': 'CNY',
}

# Операции QUIK → типы транзакций
OPERATION_MAP = {
    'купля': 'покупка', 'покупка': 'покупка',
    'продажа': 'продажа',
}

# Заголовки QUIK → ключи колонок (регистронезависимо)
_COLUMN_ALIASES = {
    'number': ('номер',),
    'isin': ('код инструмента',),
    'name': ('инструмент', 'наименование', 'название'),
    'operation': ('операция',),
    'price': ('цена',),
    'qty': ('кол-во', 'количество'),
    'filled': ('исполнено',),
    'volume': ('объем', 'объём', 'сумма'),
    'currency': ('валюта',),
    'status': ('состояние',),
    'time': ('выставлена (время)', 'время', 'выставлена'),
}

_EXECUTE_STATUS = 'исполнена'


def guess_asset_type(section, name=''):
    """Угадать тип инструмента по секции биржи в скобках.

    «БалтЛизП18 [МБ ФР: Т+: Облигации]» → 'облигация',
    «SBER [Т+: Акции]» → 'акция',
    «FXRL [Т+: Фонды]» → 'etf'. Неизвестное → ''.
    """
    s = f"{section} {name}".lower()
    if 'облигац' in s or 'офз' in s:
        return 'облигация'
    if 'фонд' in s or 'etf' in s or 'пиф' in s:
        return 'etf'
    if 'акци' in s:
        return 'акция'
    return ''


def _col_index(cell_ref):
    """Буквенная часть ссылки ячейки ('BC12') → индекс колонки (0-based)."""
    idx = 0
    for ch in cell_ref:
        if ch.isalpha():
            idx = idx * 26 + (ord(ch.upper()) - ord('A') + 1)
        else:
            break
    return idx - 1


def _parse_quik_number(value):
    """'85.65' / '85,65' / '5 738,55' → float | None."""
    if value is None:
        return None
    cleaned = str(value).strip().replace('\xa0', '').replace(' ', '')
    cleaned = cleaned.replace('\u2009', '').replace(',', '.')
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_quik_xlsx(filepath):
    """Разобрать XLSX-экспорт таблицы заявок QUIK.

    Возвращает список dict с ключами:
        number, isin, name, operation ('покупка'/'продажа'),
        price, qty, filled (исполнено, None если столбца нет),
        volume, currency (код приложения, напр. RUB), status, time.

    Бросает ValueError с понятным сообщением, если файл не похож
    на экспорт заявок QUIK.
    """
    try:
        zf = zipfile.ZipFile(filepath)
    except (zipfile.BadZipFile, OSError) as e:
        raise ValueError(f"Не удалось открыть XLSX-файл:\n{e}")

    with zf:
        names = zf.namelist()
        sheet_path = None
        for cand in ('xl/worksheets/sheet1.xml', 'xl/worksheets/sheet2.xml'):
            if cand in names:
                sheet_path = cand
                break
        if sheet_path is None:
            raise ValueError(
                "Файл не похож на XLSX-экспорт QUIK: не найден лист данных."
            )

        shared = []
        if 'xl/sharedStrings.xml' in names:
            root = ET.fromstring(zf.read('xl/sharedStrings.xml'))
            for si in root.findall(f'{_NS}si'):
                shared.append(''.join(
                    t.text or '' for t in si.iter(f'{_NS}t')
                ))

        sheet = ET.fromstring(zf.read(sheet_path))

    # Строки → списки значений ячеек (по ссылкам, с учётом пропусков)
    table = []
    for row in sheet.iter(f'{_NS}row'):
        cells = {}
        for c in row.findall(f'{_NS}c'):
            ref = c.get('r', '')
            col = _col_index(ref) if ref else len(cells)
            v = c.find(f'{_NS}v')
            val = v.text if v is not None else ''
            if c.get('t') == 's' and val != '':
                val = shared[int(val)]
            elif c.get('t') == 'inlineStr':
                is_el = c.find(f'{_NS}is')
                val = ''.join(
                    t.text or '' for t in is_el.iter(f'{_NS}t')
                ) if is_el is not None else ''
            if val is None:
                val = ''
            cells[col] = val.strip()
        if not cells:
            continue
        width = max(cells) + 1
        table.append([cells.get(i, '') for i in range(width)])

    if not table:
        raise ValueError("Файл не содержит данных.")

    # Распознавание колонок по заголовку
    header = [str(x).strip().lower() for x in table[0]]
    col_map = {}
    for key, aliases in _COLUMN_ALIASES.items():
        for i, h in enumerate(header):
            if h in aliases:
                col_map[key] = i
                break
    if 'number' not in col_map or 'operation' not in col_map:
        raise ValueError(
            "Файл не похож на экспорт заявок QUIK:\n"
            "в первой строке не найдены колонки «Номер» и «Операция»."
        )

    def cell(parts, key):
        i = col_map.get(key)
        return parts[i] if i is not None and i < len(parts) else ''

    orders = []
    for parts in table[1:]:
        number = cell(parts, 'number')
        operation_raw = cell(parts, 'operation')
        if not number or not operation_raw:
            continue  # мусорная строка (итоги и т.п.)
        operation = OPERATION_MAP.get(operation_raw.strip().lower())
        if operation is None:
            continue

        instrument = cell(parts, 'isin')          # «Код инструмента»
        # «Инструмент» с секцией: «БалтЛизП18 [МБ ФР: Т+: Облигации]»
        display = cell(parts, 'name') if 'name' in col_map else ''
        if not display and instrument:
            display = instrument
        section = ''
        m = re.search(r'\[([^\]]*)\]\s*$', display)
        if m:
            section = m.group(1).strip()
        short_name = re.sub(r'\s*\[[^\]]*\]\s*$', '', display).strip()

        currency_raw = cell(parts, 'currency').strip().upper()
        currency = CURRENCY_MAP.get(currency_raw, 'RUB')

        time_val = cell(parts, 'time').strip()
        # QUIK иногда пишет полное время «01.01.2026 11:03:16» — оставить время
        m = re.search(r'(\d{1,2}:\d{2}(?::\d{2})?)', time_val)
        time_val = m.group(1) if m else time_val

        orders.append({
            'number': number.strip(),
            'isin': instrument.strip(),
            'name': short_name or display.strip(),
            'section': section,
            'guessed_type': guess_asset_type(section, short_name),
            'operation': operation,
            'price': _parse_quik_number(cell(parts, 'price')),
            'qty': _parse_quik_number(cell(parts, 'qty')),
            'filled': (_parse_quik_number(cell(parts, 'filled'))
                       if 'filled' in col_map else None),
            'volume': _parse_quik_number(cell(parts, 'volume')),
            'currency': currency,
            'status': cell(parts, 'status').strip(),
            'time': time_val,
        })

    return orders


def find_imported_numbers(numbers):
    """Вернуть set номеров заявок, уже импортированных в transactions.

    Совпадение ищется по префиксу notes: 'QUIK №<номер> '.
    """
    result = set()
    if not numbers:
        return result
    conn = get_connection()
    cursor = conn.cursor()
    for number in numbers:
        cursor.execute(
            "SELECT 1 FROM transactions WHERE notes LIKE ? LIMIT 1",
            (f"QUIK №{number} %",),
        )
        if cursor.fetchone():
            result.add(number)
    conn.close()
    return result


def import_orders_as_transactions(orders, account_id, tx_date, type_map=None,
                                  update_portfolio=True):
    """Записать отмеченные заявки в transactions (одной транзакцией БД).

    При update_portfolio=True покупки и продажи обрабатываются как в
    приложении: создаются/докупаются/продаются позиции во вкладке «Активы»,
    меняется баланс счёта, сумма записывается в валюте счёта. При False
    заявки пишутся только в журнал транзакций.

    Тикеры из заявок, отсутствующие в реестре ticker_names, добавляются
    туда с коротким именем (до квадратной скобки, напр. «Л-Старт 02»)
    и типом из type_map (тикер → 'акция'/'облигация'/'etf', '' — не задан).
    Существующие записи не перезаписываются (имя — только если было пустым).

    Возвращает (imported_count, skipped_count, new_tickers_count, stats),
    где stats — dict c ключами created, bought, sold (списки тикеров)
    и warnings (список строк).
    """
    type_map = type_map or {}
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    imported = 0
    skipped = 0
    new_tickers = 0
    stats = {'created': [], 'bought': [], 'sold': [], 'warnings': []}
    with _write_lock:
        conn = get_connection()
        cursor = conn.cursor()
        try:
            # Данные счёта и курсы (для конвертации в валюту счёта)
            acc_currency_id = 1
            if update_portfolio:
                cursor.execute(
                    "SELECT currency_id FROM accounts WHERE id = ?",
                    (account_id,),
                )
                acc_row = cursor.fetchone()
                if acc_row:
                    acc_currency_id = acc_row["currency_id"] or 1
            acc_code = get_currency_code(acc_currency_id)
            rates = _get_rates_from_db(cursor)

            for o in orders:
                cursor.execute(
                    "SELECT 1 FROM transactions WHERE notes LIKE ? LIMIT 1",
                    (f"QUIK №{o['number']} %",),
                )
                if cursor.fetchone():
                    skipped += 1
                    continue

                # Регистрация тикера в реестре, если его ещё нет
                ticker = (o.get('isin') or '').strip()
                if ticker and o.get('name'):
                    ticker_u = ticker.upper()
                    cursor.execute(
                        "SELECT name FROM ticker_names WHERE ticker = ?",
                        (ticker_u,),
                    )
                    row = cursor.fetchone()
                    if row is None:
                        new_tickers += 1
                        cursor.execute(
                            "INSERT INTO ticker_names (ticker, name, asset_type)"
                            " VALUES (?, ?, ?)",
                            (ticker_u, o['name'], type_map.get(ticker_u, '')),
                        )
                    elif not row['name'] and o['name']:
                        cursor.execute(
                            "UPDATE ticker_names SET name = ? WHERE ticker = ?",
                            (o['name'], ticker_u),
                        )

                # Частично исполненная заявка («Снята», но «Исполнено» > 0):
                # берём исполненное количество и пропорциональную сумму —
                # «Объем» в экспорте указан на всю заявку целиком
                full_qty = o['qty'] or 0
                qty = full_qty
                filled = o.get('filled')
                if filled is not None and 0 < filled < full_qty:
                    qty = filled

                amount = o['volume']
                if amount is None:
                    amount = (o['price'] or 0) * qty
                elif qty != full_qty and full_qty > 0:
                    amount = amount * qty / full_qty

                notes = f"QUIK №{o['number']} {o['time']}"
                if qty != full_qty:
                    notes += f" · исполнено {qty:g} из {full_qty:g}"

                if not update_portfolio:
                    add_transaction_internal(
                        cursor,
                        o['operation'],
                        account_id,
                        amount,
                        get_currency_id(o['currency']),
                        ticker=o['isin'],
                        notes=notes,
                        tx_date=tx_date,
                        qty=qty,
                        price=o['price'],
                        profit=None,
                    )
                    imported += 1
                    continue

                # ── Режим портфеля: сумма в валюте счёта ──
                asset_currency_id = get_currency_id(o['currency'])
                amount_acc = compute_amount_in_account_currency(
                    amount, o['currency'], acc_code, rates
                )

                # Позиция на этом счёте (первая по тикеру)
                asset_id = None
                asset = None
                if ticker:
                    cursor.execute(
                        """
                        SELECT a.*, c.code AS currency_code
                        FROM assets a
                        LEFT JOIN currencies c ON a.currency_id = c.id
                        WHERE UPPER(a.ticker) = ? AND a.broker_id = ?
                        ORDER BY a.id LIMIT 1
                        """,
                        (ticker.upper(), account_id),
                    )
                    asset = cursor.fetchone()

                price = o['price']
                profit = None

                if o['operation'] == 'покупка':
                    if asset is not None:
                        # Докупка: средняя цена, количество, запись в buys
                        ls = (asset["lot_size"]
                              if asset["lot_size"] and asset["lot_size"] > 0
                              else 1)
                        old_qty = asset["quantity"]
                        old_avg = asset["avg_price"]
                        new_qty = old_qty + qty
                        new_avg = ((old_avg * old_qty) +
                                   (price * qty)) / new_qty if new_qty else old_avg
                        cursor.execute(
                            "UPDATE assets SET quantity=?, avg_price=? WHERE id=?",
                            (new_qty, new_avg, asset["id"]),
                        )
                        cursor.execute("""
                            INSERT INTO buys
                                (asset_id, ticker, quantity, price, currency_id,
                                 broker_id, buy_date, created_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """, (asset["id"], asset["ticker"], qty, price,
                              asset["currency_id"], account_id, tx_date, now))
                        asset_id = asset["id"]
                        stats['bought'].append(asset["ticker"])
                    elif ticker:
                        asset_type = type_map.get(ticker.upper(), '')
                        # Для облигаций в реестре lot_size — номинал
                        tn_lot = None
                        cursor.execute(
                            "SELECT asset_type, lot_size FROM ticker_names "
                            "WHERE ticker = ?",
                            (ticker.upper(),),
                        )
                        tn_row = cursor.fetchone()
                        if not asset_type and tn_row and tn_row['asset_type']:
                            asset_type = tn_row['asset_type']
                        if tn_row and tn_row['lot_size']:
                            tn_lot = tn_row['lot_size']
                        if asset_type == 'облигация':
                            fv = tn_lot if (tn_lot and tn_lot > 0) else 1000
                        else:
                            fv = 1000
                        cursor.execute("""
                            INSERT INTO assets
                                (ticker, name, asset_type, quantity, avg_price,
                                 broker_id, purchase_date, created_at,
                                 currency_id, face_value, lot_size, lot_value,
                                 list_level)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, NULL)
                        """, (ticker, o['name'], asset_type, qty, price,
                              account_id, tx_date, now, asset_currency_id,
                              fv, fv))
                        asset_id = cursor.lastrowid
                        stats['created'].append(ticker)
                    # Списание с баланса (баланс может уйти в минус)
                    cursor.execute(
                        "SELECT balance FROM accounts WHERE id = ?",
                        (account_id,),
                    )
                    bal_row = cursor.fetchone()
                    if bal_row is not None:
                        new_bal = round_price(bal_row["balance"] - amount_acc)
                        cursor.execute(
                            "UPDATE accounts SET balance = ? WHERE id = ?",
                            (new_bal, account_id),
                        )
                        if new_bal < 0:
                            stats['warnings'].append(
                                f"Покупка {ticker or o['name']}: баланс счёта "
                                f"ушёл в минус ({new_bal:.2f} {acc_code})"
                            )
                else:
                    # ── Продажа ──
                    if asset is not None:
                        asset_qty = asset["quantity"]
                        sold_qty = qty if qty <= asset_qty else asset_qty
                        if qty > asset_qty:
                            stats['warnings'].append(
                                f"Продажа {asset['ticker']}: в позиции "
                                f"{asset_qty:g} шт., продано по факту "
                                f"{sold_qty:g} шт."
                            )
                        ls = (asset["lot_size"]
                              if asset["lot_size"] and asset["lot_size"] > 0
                              else 1)
                        if asset["asset_type"] == "облигация":
                            fv = asset["face_value"] or 1000
                            unit = fv / 100
                        else:
                            unit = ls
                        if amount and qty:
                            sell_sum = amount * sold_qty / qty
                        else:
                            sell_sum = (price or 0) * sold_qty * unit
                        profit = sell_sum - asset["avg_price"] * sold_qty * unit
                        amount_acc = compute_amount_in_account_currency(
                            sell_sum, o['currency'], acc_code, rates
                        )
                        remaining = asset_qty - sold_qty
                        if remaining <= 0.000001:
                            cursor.execute(
                                "UPDATE snapshot_assets SET asset_id = NULL "
                                "WHERE asset_id = ?", (asset["id"],))
                            cursor.execute(
                                "UPDATE buys SET asset_id = NULL WHERE asset_id = ?",
                                (asset["id"],))
                            cursor.execute(
                                "UPDATE transactions SET asset_id = NULL "
                                "WHERE asset_id = ?", (asset["id"],))
                            cursor.execute(
                                "DELETE FROM assets WHERE id = ?", (asset["id"],))
                        else:
                            cursor.execute(
                                "UPDATE assets SET quantity = quantity - ? "
                                "WHERE id = ?", (sold_qty, asset["id"]))
                            asset_id = asset["id"]
                        stats['sold'].append(asset["ticker"])
                    else:
                        stats['warnings'].append(
                            f"Продажа {ticker or o['name']}: позиция на счёте "
                            f"не найдена — записана только транзакция"
                        )
                    # Зачисление на баланс
                    cursor.execute(
                        "SELECT balance FROM accounts WHERE id = ?",
                        (account_id,),
                    )
                    bal_row = cursor.fetchone()
                    if bal_row is not None:
                        new_bal = round_price(bal_row["balance"] + amount_acc)
                        cursor.execute(
                            "UPDATE accounts SET balance = ? WHERE id = ?",
                            (new_bal, account_id),
                        )

                add_transaction_internal(
                    cursor,
                    o['operation'],
                    account_id,
                    amount_acc,
                    acc_currency_id,
                    ticker=o['isin'],
                    notes=notes,
                    tx_date=tx_date,
                    asset_id=asset_id,
                    qty=qty,
                    price=o['price'],
                    profit=profit,
                )
                imported += 1
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    return imported, skipped, new_tickers, stats


def _center_over_parent(win, parent):
    """Центрировать окно win над родительским parent."""
    win.update_idletasks()
    px, py = parent.winfo_rootx(), parent.winfo_rooty()
    pw, ph = parent.winfo_width(), parent.winfo_height()
    w, h = win.winfo_width(), win.winfo_height()
    x = px + (pw - w) // 2
    y = py + (ph - h) // 2
    win.geometry(f"+{max(x, 0)}+{max(y, 0)}")


class NewTickerTypeDialog(tb.Toplevel):
    """Выбор типа инструмента для новых тикеров перед импортом.

    Параметры:
        parent: родительское окно.
        tickers: список dict (isin, name, guess) — тикеры, которых нет в реестре.

    Атрибуты после закрытия:
        cancelled: True — пользователь отменил выбор.
        choices: dict isin → тип ('акция'/'облигация'/'etf', '' — не задан).
    """

    _TYPES = ('акция', 'облигация', 'etf')
    _LATER = '(не задан)'

    def __init__(self, parent, tickers):
        super().__init__(parent)
        self.title("Новые инструменты — выбор типа")
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())
        self.cancelled = True
        self.choices = {}
        self._vars = {}

        tb.Label(
            self,
            text=(
                "Этих инструментов нет в реестре тикеров. Тип предугадан по секции "
                "биржи в названии — проверьте и при необходимости измените.\n"
                "Можно оставить «(не задан)» и заполнить позже: Настройки → Тикеры."
            ),
            foreground='gray', justify=tk.LEFT,
        ).pack(fill=tk.X, padx=10, pady=(10, 5))

        frame = tb.Frame(self)
        frame.pack(fill=tk.BOTH, expand=True, padx=10)

        tb.Label(frame, text="Код", bootstyle='primary').grid(
            row=0, column=0, sticky=tk.W, padx=5)
        tb.Label(frame, text="Инструмент", bootstyle='primary').grid(
            row=0, column=1, sticky=tk.W, padx=5)
        tb.Label(frame, text="Тип", bootstyle='primary').grid(
            row=0, column=2, sticky=tk.W, padx=5)

        values = self._TYPES + (self._LATER,)
        for i, t in enumerate(tickers, start=1):
            tb.Label(frame, text=t['isin']).grid(
                row=i, column=0, sticky=tk.W, padx=5, pady=2)
            tb.Label(frame, text=t['name']).grid(
                row=i, column=1, sticky=tk.W, padx=5, pady=2)
            guess = t.get('guess') or ''
            var = tk.StringVar(
                value=guess if guess in self._TYPES else self._LATER)
            self._vars[t['isin']] = var
            tb.Combobox(frame, textvariable=var, values=values, width=12,
                        state='readonly').grid(
                row=i, column=2, sticky=tk.W, padx=5, pady=2)

        bottom = tb.Frame(self, padding=(10, 5, 10, 10))
        bottom.pack(fill=tk.X)
        tb.Button(bottom, text="Подтвердить", command=self._ok,
                  bootstyle='primary').pack(side=tk.RIGHT, padx=(10, 0))
        tb.Button(bottom, text="Отмена", command=self.destroy,
                  bootstyle='secondary').pack(side=tk.RIGHT)

        self.grab_set()
        _center_over_parent(self, parent)

    def _ok(self):
        self.cancelled = False
        for isin, var in self._vars.items():
            t = var.get()
            self.choices[isin] = '' if t == self._LATER else t
        self.destroy()


class QuikImportDialog(tb.Toplevel):
    """Окно предпросмотра заявок QUIK с выбором и импортом в transactions.

    Параметры:
        parent: родительское окно.
        orders: список dict от parse_quik_xlsx().
        accounts: список счетов (dict: id, name, account_number).
        default_account_id: ID счёта, предвыбранного в комбобоксе.
        on_done: callback(imported, skipped, new_tickers, stats)
                  после успешного импорта.
    """

    _CHECKED = '☑'
    _UNCHECKED = '☐'

    def __init__(self, parent, orders, accounts, default_account_id=None,
                 on_done=None):
        super().__init__(parent)
        self.title("Импорт заявок QUIK")
        self.resizable(True, True)
        self.transient(parent.winfo_toplevel())
        self.orders = orders
        self.accounts = accounts
        self.default_account_id = default_account_id
        self.on_done = on_done
        self._checked = {}   # iid → bool
        self._imported_numbers = find_imported_numbers(
            [o['number'] for o in orders]
        )

        self._create_ui()

        self.grab_set()
        _center_over_parent(self, parent)

    # ─── UI ────────────────────────────────────────────────────────

    def _create_ui(self):
        # Верх: счёт и дата
        top = tb.Frame(self, padding=(10, 10, 10, 5))
        top.pack(fill=tk.X)

        tb.Label(top, text="Счёт:").grid(row=0, column=0, sticky=tk.W, padx=(0, 5))
        self._account_map = {}
        values = []
        default_display = ''
        for acc in self.accounts:
            display = acc['name']
            if acc['account_number']:
                display += f" · {acc['account_number']}"
            values.append(display)
            self._account_map[display] = acc['id']
            if self.default_account_id is not None and acc['id'] == self.default_account_id:
                default_display = display
        if not default_display and values:
            default_display = values[0]
        self.account_var = tk.StringVar(value=default_display)
        self.account_combo = tb.Combobox(
            top, textvariable=self.account_var, values=values,
            state="readonly", width=30,
        )
        self.account_combo.grid(row=0, column=1, sticky=tk.W, padx=(0, 15))

        tb.Label(top, text="Дата сделки:").grid(row=0, column=2, sticky=tk.W, padx=(0, 5))
        self.date_entry = create_date_entry(top, initial_date=datetime.now(), width=14)
        self.date_entry.grid(row=0, column=3, sticky=tk.W)

        self.apply_var = tk.BooleanVar(value=True)
        tb.Checkbutton(
            top, text="Изменять активы и баланс счёта",
            variable=self.apply_var,
        ).grid(row=1, column=0, columnspan=4, sticky=tk.W, pady=(5, 0))

        # Таблица заявок
        columns = ('mark', 'number', 'isin', 'name', 'operation',
                   'price', 'qty', 'filled', 'volume', 'time', 'status')
        headers = ('', 'Номер', 'Код', 'Инструмент', 'Операция',
                   'Цена', 'Кол-во', 'Исполнено', 'Объём', 'Время',
                   'Состояние')
        widths = (36, 130, 120, 200, 80, 70, 65, 90, 90, 70, 90)
        from table_utils import apply_zebra
        self.tree = tb.Treeview(self, columns=columns, show='headings',
                                height=min(14, max(6, len(self.orders))),
                                selectmode='none')
        for col, head, width in zip(columns, headers, widths):
            anchor = tk.E if col in ('price', 'qty', 'filled', 'volume') else tk.W
            self.tree.heading(col, text=head)
            self.tree.column(col, width=width, anchor=anchor,
                             stretch=(col == 'name'))
        vsb = tb.Scrollbar(self, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        vsb.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 10), pady=5)

        self.tree.tag_configure('dup', foreground='gray')

        for i, o in enumerate(self.orders):
            is_dup = o['number'] in self._imported_numbers
            default_on = (not is_dup and self._is_executed(o))
            self._checked[str(i)] = default_on
            self.tree.insert('', tk.END, iid=str(i), tags=('dup',) if is_dup else (), values=(
                self._CHECKED if default_on else self._UNCHECKED,
                o['number'],
                o['isin'],
                o['name'],
                'Покупка' if o['operation'] == 'покупка' else 'Продажа',
                f"{o['price']:.2f}" if o['price'] is not None else '',
                f"{o['qty']:g}" if o['qty'] is not None else '',
                f"{o['filled']:g}" if o.get('filled') is not None else '',
                f"{o['volume']:.2f}" if o['volume'] is not None else '',
                o['time'],
                o['status'] + (' · уже импортирована' if is_dup else ''),
            ))

        self.tree.bind('<ButtonRelease-1>', self._on_row_click)
        self.tree.bind('<space>', self._on_space)
        apply_zebra(self.tree)

        # Низ: кнопки
        bottom = tb.Frame(self, padding=(10, 5, 10, 10))
        bottom.pack(fill=tk.X)
        tb.Button(
            bottom, text="Отметить все исполненные",
            command=self._check_all_executed, bootstyle="secondary-outline",
        ).pack(side=tk.LEFT)
        self.import_btn = tb.Button(
            bottom, text="Импорт", command=self._do_import,
            bootstyle="primary",
        )
        self.import_btn.pack(side=tk.RIGHT, padx=(10, 0))
        tb.Button(
            bottom, text="Отмена", command=self.destroy, bootstyle="secondary",
        ).pack(side=tk.RIGHT)

    # ─── События таблицы ───────────────────────────────────────────

    def _on_row_click(self, event):
        iid = self.tree.identify_row(event.y)
        if iid:
            self._toggle(iid)

    def _on_space(self, _event):
        # Space без выделения (selectmode='none') — неактивно; клик основной
        return 'break'

    def _toggle(self, iid):
        if 'dup' in self.tree.item(iid, 'tags'):
            return
        self._checked[iid] = not self._checked.get(iid, False)
        values = list(self.tree.item(iid, 'values'))
        values[0] = self._CHECKED if self._checked[iid] else self._UNCHECKED
        self.tree.item(iid, values=values)

    def _check_all_executed(self):
        for i, o in enumerate(self.orders):
            iid = str(i)
            if 'dup' in self.tree.item(iid, 'tags'):
                continue
            on = self._is_executed(o)
            self._checked[iid] = on
            values = list(self.tree.item(iid, 'values'))
            values[0] = self._CHECKED if on else self._UNCHECKED
            self.tree.item(iid, values=values)

    @staticmethod
    def _is_executed(o):
        """Заявка к импорту по умолчанию: «Исполнена» или есть исполненное
        количество (частично исполненная снятая заявка)."""
        if o['status'].strip().lower() == _EXECUTE_STATUS:
            return True
        filled = o.get('filled')
        return filled is not None and filled > 0

    # ─── Импорт ────────────────────────────────────────────────────

    def _do_import(self):
        account_id = self._account_map.get(self.account_var.get())
        if not account_id:
            messagebox.showwarning("Счёт не выбран",
                                   "Выберите счёт для импорта.", parent=self)
            return

        selected = [
            self.orders[int(iid)]
            for iid, on in self._checked.items() if on
        ]
        if not selected:
            messagebox.showwarning(
                "Нет заявок", "Отметьте галочками заявки для импорта.",
                parent=self,
            )
            return

        # Новые тикеры среди отмеченных → диалог выбора типа
        type_map = {}
        new_ticker_rows = []
        seen = set()
        conn = get_connection()
        cursor = conn.cursor()
        for o in selected:
            t = (o.get('isin') or '').strip().upper()
            if not t or t in seen:
                continue
            seen.add(t)
            cursor.execute(
                "SELECT 1 FROM ticker_names WHERE ticker = ?", (t,))
            if not cursor.fetchone():
                new_ticker_rows.append({
                    'isin': t,
                    'name': o.get('name') or '',
                    'guess': o.get('guessed_type') or '',
                })
        conn.close()

        if new_ticker_rows:
            dlg = NewTickerTypeDialog(self, new_ticker_rows)
            self.wait_window(dlg)
            self.grab_set()
            if dlg.cancelled:
                return
            type_map = dlg.choices

        tx_date = self.date_entry.get_date().strftime("%Y-%m-%d")
        try:
            imported, skipped, new_tickers, stats = import_orders_as_transactions(
                selected, account_id, tx_date, type_map=type_map,
                update_portfolio=self.apply_var.get(),
            )
        except Exception as e:
            messagebox.showerror(
                "Ошибка импорта", f"Не удалось импортировать заявки:\n{e}",
                parent=self,
            )
            return

        self.destroy()
        if self.on_done:
            self.on_done(imported, skipped, new_tickers, stats)

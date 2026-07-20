"""Проверка актуальности данных и сохранение среза портфеля."""
import tkinter as tk
import threading
import queue
from datetime import datetime, timedelta
from tkinter import messagebox
import ttkbootstrap as tb

from database import (
    get_connection, get_all_assets, update_asset_price, save_snapshot,
    get_latest_snapshot_month, _MONTH_NAMES,
)
from api_client import fetch_cbr_exchange_rates, fetch_price, is_connected

STALE_DAYS = 1


# ── Проверки актуальности ───────────────────────────────────────────

def _parse_date(s):
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s.strip(), fmt)
        except (ValueError, TypeError):
            continue
    return None


def _is_stale(date_str):
    dt = _parse_date(date_str)
    if dt is None:
        return True
    return (datetime.now() - dt) > timedelta(days=STALE_DAYS)


def get_assets_update_date():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT MAX(last_update) as mx FROM assets")
    row = cursor.fetchone()
    conn.close()
    return row["mx"] if row else None


def get_rates_update_date():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT updated_at FROM settings WHERE setting_key = 'usd_rub_rate'")
    row = cursor.fetchone()
    conn.close()
    return row["updated_at"] if row else None


# ── Обновление данных ──────────────────────────────────────────────

def _refresh_rates():
    rates = fetch_cbr_exchange_rates()
    if not rates:
        return False
    today = datetime.now().strftime("%Y-%m-%d")
    conn = get_connection()
    cursor = conn.cursor()
    for key, cur in (("usd_rub_rate", "USD"), ("eur_rub_rate", "EUR"), ("cny_rub_rate", "CNY")):
        if cur in rates:
            val = str(round(rates[cur], 4))
            cursor.execute("""
                INSERT INTO settings (setting_key, setting_value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(setting_key) DO UPDATE SET setting_value = ?, updated_at = ?
            """, (key, val, today, val, today))
    conn.commit()
    conn.close()
    return True


# ── Окно прогресса обновления цен ──────────────────────────────────

class _PriceRefreshDialog:
    """Всплывающее окно с прогресс-баром для обновления цен."""

    def __init__(self, parent):
        self.dialog = tb.Toplevel(parent)
        self.dialog.title("Обновление цен активов")
        self.dialog.geometry("450x120")
        self.dialog.transient(parent)
        self.dialog.grab_set()
        # Запретить закрытие окна крестиком во время обновления
        self.dialog.protocol("WM_DELETE_WINDOW", lambda: None)

        tb.Label(
            self.dialog,
            text="Обновление текущих цен с Московской биржи...",
            padding=10,
        ).pack(fill=tk.X)

        self.progress = tb.Progressbar(
            self.dialog, mode='determinate', maximum=100,
        )
        self.progress.pack(fill=tk.X, padx=10, pady=5)

        self.status_label = tb.Label(
            self.dialog, text="Ожидание...", foreground="gray",
        )
        self.status_label.pack(pady=5)

    def set_range(self, total):
        self.total = total
        self.count = 0

    def update(self, ticker, price):
        self.count += 1
        if self.total > 0:
            self.progress['value'] = (self.count / self.total) * 100
        self.status_label.configure(text=f"[{self.count}/{self.total}] {ticker}: {price:.2f}")

    def set_error(self, ticker):
        self.count += 1
        if self.total > 0:
            self.progress['value'] = (self.count / self.total) * 100
        self.status_label.configure(text=f"[{self.count}/{self.total}] {ticker}: не найдено")

    def close(self):
        self.dialog.destroy()


# ── Обновление цен с прогрессом (асинхронный паттерн) ─────────────

def _refresh_prices_with_progress(parent, status_var=None):
    """Асинхронно обновить цены всех активов с модальным прогресс-баром.

    Использует фоновый поток + queue.Queue + after-polling (тот же паттерн,
    что в assets_view._refresh_prices). wait_window() блокирует возврат
    до завершения потока, но событийный цикл Tkinter крутится — окно
    можно перетаскивать, UI отзывчив.

    Returns:
        (success, not_found, completed: bool)
    """
    if not is_connected():
        if status_var:
            status_var.set("Нет интернета. Цены не обновлены.")
        return 0, 0, False

    assets = get_all_assets()
    if not assets:
        return 0, 0, True

    total = len(assets)
    progress_dialog = _PriceRefreshDialog(parent)
    progress_dialog.set_range(total)

    result = {'done': False, 'success': 0, 'not_found': 0}
    msg_queue = queue.Queue()

    def worker():
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        success = not_found = 0
        for asset in assets:
            try:
                price, error = fetch_price(asset["ticker"], asset["asset_type"])
                if price is not None:
                    update_asset_price(asset["id"], price, now)
                    success += 1
                    msg_queue.put(("progress", asset["ticker"], price))
                else:
                    not_found += 1
                    msg_queue.put(("error", asset["ticker"]))
            except Exception:
                not_found += 1
                msg_queue.put(("error", asset["ticker"]))
        msg_queue.put(("done", success, not_found))

    threading.Thread(target=worker, daemon=True).start()

    def poll():
        try:
            while True:
                msg = msg_queue.get_nowait()
                if msg[0] == "done":
                    result['done'] = True
                    result['success'] = msg[1]
                    result['not_found'] = msg[2]
                    progress_dialog.close()
                    return
                elif msg[0] == "progress":
                    progress_dialog.update(msg[1], msg[2])
                elif msg[0] == "error":
                    progress_dialog.set_error(msg[1])
        except queue.Empty:
            pass

        if not result['done']:
            parent.after(100, poll)

    poll()
    # Блокирует до close(), но событийный цикл крутится — UI отзывчив
    progress_dialog.dialog.wait_window()

    success = result['success']
    not_found = result['not_found']
    if status_var:
        status_var.set(f"Цены обновлены: {success} успешно, {not_found} не найдено")

    return success, not_found, True


# ── Диалог пропуска предыдущего месяца ────────────────────────────


def _ask_missing_month_dialog(parent, month_name):
    """Диалог «Нет среза за {month_name}. Создать?».

    Возвращает 'previous' (создать за прошлый месяц),
    'current' (пропустить — создать за текущий) или 'cancel' (отмена).
    """
    current_month = _MONTH_NAMES[datetime.now().month - 1]

    result = {'value': None}

    dlg = tb.Toplevel(parent)
    dlg.title("Пропущен срез")
    dlg.resizable(False, False)
    dlg.transient(parent)
    dlg.grab_set()

    tb.Label(
        dlg,
        text=f"Нет среза за {month_name}. Создать его сейчас?",
        justify=tk.LEFT,
    ).pack(fill=tk.X, padx=20, pady=(15, 5))

    tb.Label(
        dlg,
        text="(Сохранится текущее состояние портфеля с датой конца месяца.)",
        foreground="gray",
        wraplength=370,
        justify=tk.LEFT,
    ).pack(fill=tk.X, padx=20)

    btn_frame = tb.Frame(dlg)
    btn_frame.pack(pady=12)

    def on_previous():
        result['value'] = 'previous'
        dlg.destroy()

    def on_current():
        result['value'] = 'current'
        dlg.destroy()

    def on_cancel():
        result['value'] = 'cancel'
        dlg.destroy()

    tb.Button(
        btn_frame,
        text=f"Да, за {month_name}",
        command=on_previous,
        bootstyle="success",
        width=20,
    ).pack(side=tk.LEFT, padx=5)

    tb.Button(
        btn_frame,
        text=f"За текущий ({current_month})",
        command=on_current,
        bootstyle="info",
        width=20,
    ).pack(side=tk.LEFT, padx=5)

    tb.Button(
        btn_frame,
        text="Отмена",
        command=on_cancel,
        bootstyle="danger",
        width=12,
    ).pack(side=tk.LEFT, padx=5)

    dlg.wait_window()
    return result['value']


# ── Основная функция ──────────────────────────────────────────────

def save_snapshot_full(parent, status_var=None):
    """
    Проверить актуальность данных, обновить если нужно, сохранить срез.

    Args:
        parent: родительское окно для диалогов.
        status_var: tk.StringVar для статуса (опционально).

    Returns:
        (accounts_count, total_portfolio) при успехе,
        None если отменено пользователем.
    """
    import tkinter as tk

    # 0) Проверка пропущенного предыдущего месяца
    today = datetime.now().date()
    prev_year = today.year - 1 if today.month == 1 else today.year
    prev_month = 12 if today.month == 1 else today.month - 1
    prev_ym = f"{prev_year:04d}-{prev_month:02d}"

    latest_ym = get_latest_snapshot_month()
    target_ym = None
    if latest_ym is None or latest_ym < prev_ym:
        month_name = _MONTH_NAMES[prev_month - 1]
        choice = _ask_missing_month_dialog(parent, month_name)
        if choice == 'cancel':
            return None
        if choice == 'previous':
            target_ym = prev_ym

    # Проверки актуальности — только для текущего месяца (не для бэкфилла)
    if not target_ym:
        # 1) Цены активов
        assets_date = get_assets_update_date()
        if _is_stale(assets_date):
            date_display = assets_date[:10] if assets_date else "никогда"
            if not messagebox.askyesno(
                "Устаревшие цены",
                f"Цены активов не обновлялись более {STALE_DAYS} дней\n"
                f"(последнее обновление: {date_display}).\n\n"
                f"Обновить цены перед сохранением среза?",
                parent=parent,
            ):
                return None

            _refresh_prices_with_progress(parent, status_var)
            # Обновить таблицу активов сразу после обновления цен
            if hasattr(parent, 'refresh'):
                try:
                    parent.refresh()
                except Exception:
                    pass

        # 2) Курсы валют — авто-обновление без диалога (один быстрый запрос к ЦБ РФ)
        rates_date = get_rates_update_date()
        if _is_stale(rates_date):
            _refresh_rates()

    # 3) Сохранение
    try:
        accounts_count, total_portfolio = save_snapshot(target_ym=target_ym)
        if accounts_count == 0:
            return 0, 0.0

        if target_ym:
            try:
                yr, mo = int(target_ym[:4]), int(target_ym[5:7])
            except ValueError:
                yr, mo = datetime.now().year, datetime.now().month
            display_month_name = _MONTH_NAMES[mo - 1]
            display_ym = target_ym
        else:
            display_month_name = _MONTH_NAMES[datetime.now().month - 1]
            display_ym = datetime.now().strftime("%Y-%m")

        messagebox.showinfo(
            "Успех",
            f"Срез на {display_month_name} сохранён\n"
            f"Счетов: {accounts_count}\n"
            f"Итого портфель: {total_portfolio:.2f} ₽",
            parent=parent,
        )

        if status_var:
            status_var.set(f"Срез на {display_ym}: {accounts_count} счетов, {total_portfolio:.2f} ₽")

        return accounts_count, total_portfolio
    except Exception as e:
        messagebox.showerror("Ошибка", f"Не удалось сохранить срез: {e}", parent=parent)
        return None

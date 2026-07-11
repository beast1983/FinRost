"""Проверка актуальности данных и сохранение среза портфеля."""
from datetime import datetime, timedelta
from tkinter import messagebox
import ttkbootstrap as tb

from database import (
    get_connection, get_all_assets, update_asset_price, save_snapshot,
)
from api_client import fetch_cbr_exchange_rates, fetch_price, is_connected

STALE_DAYS = 7


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
        self.dialog.update_idletasks()

    def set_error(self, ticker):
        self.count += 1
        if self.total > 0:
            self.progress['value'] = (self.count / self.total) * 100
        self.status_label.configure(text=f"[{self.count}/{self.total}] {ticker}: не найдено")
        self.dialog.update_idletasks()

    def close(self):
        self.dialog.destroy()


# ── Обновление цен с прогрессом ────────────────────────────────────

def _refresh_prices_with_progress(parent, status_var=None):
    if not is_connected():
        if status_var:
            status_var.set("Нет интернета. Цены не обновлены.")
        return 0, 0, False

    assets = get_all_assets()
    if not assets:
        return 0, 0, True

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total = len(assets)
    success = not_found = 0

    # Открываем окно прогресса
    progress_dialog = _PriceRefreshDialog(parent)
    progress_dialog.set_range(total)

    for asset in assets:
        try:
            price, error = fetch_price(asset["ticker"], asset["asset_type"])
            if price is not None:
                update_asset_price(asset["id"], price, now)
                success += 1
                progress_dialog.update(asset["ticker"], price)
            else:
                not_found += 1
                progress_dialog.set_error(asset["ticker"])
        except Exception:
            progress_dialog.set_error(asset["ticker"])

    progress_dialog.close()

    if status_var:
        status_var.set(f"Цены обновлены: {success} успешно, {not_found} не найдено")

    return success, not_found, True


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

    # 2) Курсы валют
    rates_date = get_rates_update_date()
    if _is_stale(rates_date):
        date_display = rates_date[:10] if rates_date else "никогда"
        if not messagebox.askyesno(
            "Устаревшие курсы",
            f"Курсы валют не обновлялись более {STALE_DAYS} дней\n"
            f"(последнее обновление: {date_display}).\n\n"
            f"Обновить курсы перед сохранением среза?",
            parent=parent,
        ):
            return None

        _refresh_rates()

    # 3) Сохранение
    try:
        accounts_count, total_portfolio = save_snapshot()
        if accounts_count == 0:
            return 0, 0.0

        month_name = datetime.now().strftime("%B")
        year_month = datetime.now().strftime("%Y-%m")

        messagebox.showinfo(
            "Успех",
            f"Срез на {month_name} сохранён\n"
            f"Счетов: {accounts_count}\n"
            f"Итого портфель: {total_portfolio:.2f} ₽",
            parent=parent,
        )

        if status_var:
            status_var.set(f"Срез на {year_month}: {accounts_count} счетов, {total_portfolio:.2f} ₽")

        return accounts_count, total_portfolio
    except Exception as e:
        messagebox.showerror("Ошибка", f"Не удалось сохранить срез: {e}", parent=parent)
        return None

import tkinter as tk
import ttkbootstrap as tb
from tkinter import messagebox, filedialog
import matplotlib
matplotlib.use('TkAgg')
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from database import (
    get_all_assets, get_connection, get_all_accounts,
    get_exchange_rates, calculate_total_in_rubles,
    get_snapshot_years, get_transaction_years,
    import_asset_slices, import_incomes,
    get_all_ticker_names, import_ticker_names, add_ticker_name, update_ticker_name, delete_ticker_name, rename_ticker, get_ticker_name, get_ticker_info,
    update_ticker_from_moex, convert_placeholder_tickers,
    get_db_path, backup_database,
    get_drawdown_limit, set_drawdown_limit,
    upsert_rate_history, get_rate_history, get_rate_history_years,
    _write_lock,
)
from api_client import fetch_cbr_exchange_rates, fetch_ticker_static
from datetime import datetime

RATE_HISTORY_CURRENCIES = ('USD', 'EUR', 'CNY')

MONTHS_RU = [
    'Янв', 'Фев', 'Мар', 'Апр', 'Май', 'Июн',
    'Июл', 'Авг', 'Сен', 'Окт', 'Ноя', 'Дек',
]


def _bind_entry_context_menu(widget):
    def do_copy():
        widget.event_generate('<<Copy>>')
    def do_paste(event=None):
        try:
            clipboard = widget.winfo_toplevel().clipboard_get()
            cursor_pos = widget.index(tk.INSERT)
            widget.insert(cursor_pos, clipboard)
        except tk.TclError:
            pass
    def do_cut():
        widget.event_generate('<<Cut>>')
    def show_menu(event):
        menu = tk.Menu(widget, tearoff=0)
        menu.add_command(label="Копировать", command=do_copy)
        menu.add_command(label="Вставить", command=do_paste)
        menu.add_command(label="Вырезать", command=do_cut)
        menu.post(event.x_root, event.y_root)
    widget.bind("<Button-3>", show_menu)


_RU_KEYWORDS = (
    'актив', 'тип', 'валюта', 'купон', 'дивиденд',
    'баланс', 'акция', 'облигация', 'фонд', 'индекс', 'доход',
)


def _detect_encoding(filepath):
    """Определить кодировку файла: UTF-8 → UTF-16 → cp1251/cp866 по ключевым словам."""
    with open(filepath, 'rb') as f:
        raw = f.read()

    # 1) UTF-8 (строго)
    try:
        raw.decode('utf-8-sig')
        return 'utf-8-sig'
    except (UnicodeDecodeError, LookupError):
        pass

    # 2) UTF-16
    try:
        raw.decode('utf-16')
        return 'utf-16'
    except (UnicodeDecodeError, LookupError):
        pass

    # 3) cp1251 vs cp866 — по наличию русских ключевых слов
    best_enc, best_score = 'cp1251', 0
    for enc in ('cp1251', 'cp866'):
        text = raw.decode(enc, errors='ignore').lower()
        score = sum(text.count(kw) for kw in _RU_KEYWORDS)
        if score > best_score:
            best_score = score
            best_enc = enc
    return best_enc


def _parse_number(value):
    """Распарсить число в российском формате: '2 021,88' → 2021.88."""
    if not value or not value.strip():
        return None
    cleaned = value.replace('\xa0', '').replace(' ', '').replace('\u2009', '')
    cleaned = cleaned.replace(',', '.')
    try:
        return float(cleaned)
    except ValueError:
        return None


def _is_header_row(parts):
    """Проверить, является ли строка заголовком CSV."""
    if len(parts) < 4:
        return False
    return (parts[0].strip().lower() == 'актив' and
            parts[2].strip().lower() == 'тип' and
            parts[3].strip().lower() == 'валюта')


class SettingsView(tb.Frame):
    """Окно настроек с вкладками: Валюты, Хранилище, Тикеры."""

    def __init__(self, parent, controller=None):
        super().__init__(parent)
        self.controller = controller
        self._create_ui()

    def _create_ui(self):
        """Создание интерфейса с вкладками."""
        self.notebook = tb.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # 1) Валюты
        self.currencies_tab = CurrenciesSettingsTab(self.notebook, self)
        self.notebook.add(self.currencies_tab, text="Валюты")

        # 2) Хранилище + Импорт + Сопоставление тикеров
        self.storage_tab = StorageSettingsTab(self.notebook, self)
        self.notebook.add(self.storage_tab, text="Хранилище")

        # 3) Тикеры
        self.tickers_tab = TickerRegistryTab(self.notebook)
        self.notebook.add(self.tickers_tab, text="Тикеры")


class CurrenciesSettingsTab(tb.Frame):
    """Вкладка настройки валют."""

    def __init__(self, parent, settings_view):
        super().__init__(parent)
        self.settings_view = settings_view

        # Курс USD/RUB
        self.usd_rub_rate_var = tk.StringVar(value="90.0")
        # Курс EUR/RUB
        self.eur_rub_rate_var = tk.StringVar(value="100.0")
        # Курс CNY/RUB
        self.cny_rub_rate_var = tk.StringVar(value="12.0")
        # Список валют
        self.currencies = {
            "RUB": tk.BooleanVar(value=True),
            "USD": tk.BooleanVar(value=True),
            "EUR": tk.BooleanVar(value=False),
        }
        self.last_update_date_var = tk.StringVar(value=datetime.now().strftime("%Y-%m-%d"))
        self._usd_status_var = tk.StringVar(value="")
        self.drawdown_limit_var = tk.StringVar(value="20.0")

        self._create_ui()
        self._load_rates_from_db()

    def _create_ui(self):
        """Создание интерфейса вкладки валют."""
        # Рамка со всеми тремя курсами в одну строку
        rates_frame = tb.LabelFrame(self, text="Курсы валют", padx=10, pady=10)
        rates_frame.pack(fill=tk.X, padx=5, pady=5)

        currencies_info = [
            ("USD", self.usd_rub_rate_var, "₽ за 1 USD"),
            ("EUR", self.eur_rub_rate_var, "₽ за 1 EUR"),
            ("CNY", self.cny_rub_rate_var, "₽ за 1 CNY"),
        ]

        for col, (label, var, suffix) in enumerate(currencies_info):
            rates_frame.columnconfigure(col, weight=1)
            col_frame = tb.LabelFrame(rates_frame, text=label, padx=8, pady=8)
            col_frame.grid(row=0, column=col, padx=8, pady=5, sticky=tk.NSEW)

            tb.Label(col_frame, text="Курс:").grid(row=0, column=0, sticky=tk.W, pady=3)
            entry = tb.Entry(col_frame, textvariable=var, width=12)
            entry.grid(row=0, column=1, padx=5, pady=3)
            _bind_entry_context_menu(entry)
            tb.Label(col_frame, text=suffix, foreground="gray").grid(
                row=1, column=0, columnspan=2, sticky=tk.W, pady=2
            )

        # Дата обновления и статус
        date_frame = tb.Frame(self)
        date_frame.pack(fill=tk.X, padx=10, pady=(5, 0))
        tb.Label(date_frame, text="Дата обновления:").pack(side=tk.LEFT)
        tb.Label(date_frame, textvariable=self.last_update_date_var).pack(side=tk.LEFT, padx=5)
        tb.Label(date_frame, textvariable=self._usd_status_var, foreground="gray").pack(side=tk.LEFT)

        # ─── Лимит просадки ───
        limit_frame = tb.LabelFrame(self, text="Лимит просадки", padx=10, pady=10)
        limit_frame.pack(fill=tk.X, padx=5, pady=5)

        tb.Label(
            limit_frame,
            text="Максимальное падение цены актива от средней цены покупки,\n"
                 "после которого он помечается как кандидат на продажу.",
            foreground="gray", justify=tk.LEFT,
        ).grid(row=0, column=0, columnspan=2, sticky=tk.W, padx=5, pady=2)

        tb.Label(limit_frame, text="Порог, %:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=5)
        limit_entry = tb.Entry(limit_frame, textvariable=self.drawdown_limit_var, width=10)
        limit_entry.grid(row=1, column=1, sticky=tk.W, padx=5, pady=5)
        _bind_entry_context_menu(limit_entry)

        # Общие кнопки
        btn_frame = tb.Frame(self)
        btn_frame.pack(pady=12)
        tb.Button(
            btn_frame, text="🔄 Обновить курсы",
            command=self._refresh_all_rates, bootstyle="info"
        ).pack(side=tk.LEFT, padx=10)
        tb.Button(
            btn_frame, text="Сохранить",
            command=self._save_all_rates, bootstyle="success"
        ).pack(side=tk.LEFT, padx=10)

        # ─── История курсов (график) ───
        self._create_rate_chart()

    def _create_rate_chart(self):
        """Панель «История курсов»: фильтры + линейный график по месяцам."""
        chart_frame = tb.LabelFrame(self, text="История курсов", padx=10, pady=10)
        chart_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        filter_frame = tb.Frame(chart_frame)
        filter_frame.pack(fill=tk.X, pady=(0, 5))

        tb.Label(filter_frame, text="Валюта:").pack(side=tk.LEFT, padx=(0, 4))
        self.rate_currency_var = tk.StringVar(value="USD")
        self.rate_currency_combo = tb.Combobox(
            filter_frame, textvariable=self.rate_currency_var,
            values=list(RATE_HISTORY_CURRENCIES) + ["Все"],
            state="readonly", width=8, justify=tk.CENTER,
        )
        self.rate_currency_combo.pack(side=tk.LEFT, padx=(0, 15))
        self.rate_currency_combo.bind('<<ComboboxSelected>>', lambda e: self._refresh_rate_chart())

        tb.Label(filter_frame, text="Год:").pack(side=tk.LEFT, padx=(0, 4))
        self.rate_year_from_combo = tb.Combobox(
            filter_frame, state="readonly", width=8, justify=tk.CENTER,
        )
        self.rate_year_from_combo.pack(side=tk.LEFT, padx=(0, 2))
        tb.Label(filter_frame, text="—").pack(side=tk.LEFT, padx=2)
        self.rate_year_to_combo = tb.Combobox(
            filter_frame, state="readonly", width=8, justify=tk.CENTER,
        )
        self.rate_year_to_combo.pack(side=tk.LEFT, padx=(2, 0))
        self.rate_year_from_combo.bind('<<ComboboxSelected>>', lambda e: self._refresh_rate_chart())
        self.rate_year_to_combo.bind('<<ComboboxSelected>>', lambda e: self._refresh_rate_chart())

        self.rate_figure = Figure(figsize=(8, 3))
        self.rate_ax = self.rate_figure.add_subplot(111)
        self.rate_canvas = FigureCanvasTkAgg(self.rate_figure, master=chart_frame)
        self.rate_canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        self._populate_rate_chart_years()
        self._refresh_rate_chart()

    def _populate_rate_chart_years(self):
        """Заполнить комбобоксы годов из истории курсов."""
        years = get_rate_history_years()
        if not years:
            years = [str(datetime.now().year)]
        self.rate_year_from_combo['values'] = years
        self.rate_year_to_combo['values'] = years
        self.rate_year_from_combo.set(years[-1])
        self.rate_year_to_combo.set(years[0])

    def _refresh_rate_chart(self):
        """Перерисовать график истории курсов по фильтрам."""
        currency = self.rate_currency_var.get()
        year_from = self.rate_year_from_combo.get()
        year_to = self.rate_year_to_combo.get()

        if not year_from or not year_to:
            return
        if int(year_from) > int(year_to):
            messagebox.showwarning("Неверный период", "Год «С» не может быть больше года «По»")
            return

        currencies = RATE_HISTORY_CURRENCIES if currency == "Все" else (currency,)
        series = {}
        for cur in currencies:
            series[cur] = get_rate_history(cur, year_from, year_to)

        self.rate_ax.clear()

        has_data = any(series.values())
        if not has_data:
            self.rate_ax.set_title("Нет данных — история начнёт накапливаться после обновления курсов")
            self.rate_ax.set_xticks([])
            self.rate_ax.set_yticks([])
            self.rate_figure.tight_layout()
            self.rate_canvas.draw()
            return

        all_months = sorted({m for pts in series.values() for m, _ in pts})
        x_index = {m: i for i, m in enumerate(all_months)}

        for cur in currencies:
            pts = series.get(cur) or []
            if not pts:
                continue
            xs = [x_index[m] for m, _ in pts]
            ys = [r for _, r in pts]
            self.rate_ax.plot(xs, ys, marker='o', linewidth=1.8, markersize=4, label=cur)

        labels = []
        for m in all_months:
            month_num = int(m.split('-')[1])
            labels.append(f"{MONTHS_RU[month_num - 1]} {m[2:4]}")
        self.rate_ax.set_xticks(range(len(labels)))
        self.rate_ax.set_xticklabels(labels, rotation=45, ha='right', fontsize=8)
        self.rate_ax.set_ylabel("₽ за единицу")
        self.rate_ax.grid(True, alpha=0.3)
        title_currency = "USD / EUR / CNY" if currency == "Все" else currency
        if year_from == year_to:
            self.rate_ax.set_title(f"Курс {title_currency} — {year_from} г.", fontsize=12)
        else:
            self.rate_ax.set_title(f"Курс {title_currency} — {year_from}–{year_to} г.", fontsize=12)
        if currency == "Все":
            self.rate_ax.legend(fontsize=9)
        self.rate_figure.tight_layout()
        self.rate_canvas.draw()

    def _record_rate_history(self, rates):
        """Записать точки истории за текущий месяц и перерисовать график."""
        for cur, rate in rates.items():
            if cur in RATE_HISTORY_CURRENCIES:
                try:
                    upsert_rate_history(cur, rate)
                except Exception:
                    pass
        self._populate_rate_chart_years()
        self._refresh_rate_chart()

    def _load_rates_from_db(self):
        try:
            conn = get_connection()
            cursor = conn.cursor()

            # USD курс
            cursor.execute("SELECT setting_value, updated_at FROM settings WHERE setting_key = 'usd_rub_rate'")
            row = cursor.fetchone()
            if row:
                self.usd_rub_rate_var.set(str(row["setting_value"]))
                self.last_update_date_var.set(row["updated_at"] or datetime.now().strftime("%Y-%m-%d"))

            # EUR курс
            cursor.execute("SELECT setting_value, updated_at FROM settings WHERE setting_key = 'eur_rub_rate'")
            row = cursor.fetchone()
            if row:
                self.eur_rub_rate_var.set(str(row["setting_value"]))

            # CNY курс
            cursor.execute("SELECT setting_value, updated_at FROM settings WHERE setting_key = 'cny_rub_rate'")
            row = cursor.fetchone()
            if row:
                self.cny_rub_rate_var.set(str(row["setting_value"]))

            self._usd_status_var.set("")
            # Лимит просадки
            cursor.execute("SELECT setting_value FROM settings WHERE setting_key = 'drawdown_limit_pct'")
            row = cursor.fetchone()
            if row:
                self.drawdown_limit_var.set(str(row["setting_value"]))
            conn.close()
        except Exception:
            pass

    def _refresh_usd_rate(self):
        """Обновление курса USD через ЦБ РФ."""
        self._usd_status_var.set("Загрузка...")
        self.update()

        rates = fetch_cbr_exchange_rates()

        if rates and "USD" in rates:
            new_rate = rates["USD"]
            self.usd_rub_rate_var.set(str(round(new_rate, 4)))
            today = datetime.now().strftime("%Y-%m-%d")
            self.last_update_date_var.set(today)

            # Сохраняем в БД
            with _write_lock:
                conn = get_connection()
                cursor = conn.cursor()
                try:
                    cursor.execute("""
                        INSERT INTO settings (setting_key, setting_value, updated_at)
                        VALUES ('usd_rub_rate', ?, ?)
                        ON CONFLICT(setting_key) DO UPDATE SET setting_value = ?, updated_at = ?
                    """, (str(round(new_rate, 4)), today, str(round(new_rate, 4)), today))
                    conn.commit()
                    self._usd_status_var.set("✓ Обновлено")
                    messagebox.showinfo("Успех", f"Курс USD обновлён с ЦБ РФ:\n{new_rate:.4f} ₽ за 1 USD")
                except Exception as e:
                    conn.rollback()
                    messagebox.showerror("Ошибка", f"Не удалось сохранить курс: {e}")
                finally:
                    conn.close()
        else:
            self._usd_status_var.set("✗ Ошибка")
            messagebox.showwarning("Не обновлено",
                                   "Не удалось получить курс USD с ЦБ РФ.\n"
                                   "Проверьте подключение к интернету.\n\n"
                                   "Будет использован старый курс.")

    def _refresh_eur_rate(self):
        """Обновление курса EUR через ЦБ РФ."""
        rates = fetch_cbr_exchange_rates()

        if rates and "EUR" in rates:
            new_rate = rates["EUR"]
            self.eur_rub_rate_var.set(str(round(new_rate, 4)))
            today = datetime.now().strftime("%Y-%m-%d")
            self.last_update_date_var.set(today)

            # Сохраняем в БД
            with _write_lock:
                conn = get_connection()
                cursor = conn.cursor()
                try:
                    cursor.execute("""
                        INSERT INTO settings (setting_key, setting_value, updated_at)
                        VALUES ('eur_rub_rate', ?, ?)
                        ON CONFLICT(setting_key) DO UPDATE SET setting_value = ?, updated_at = ?
                    """, (str(round(new_rate, 4)), today, str(round(new_rate, 4)), today))
                    conn.commit()
                    messagebox.showinfo("Успех", f"Курс EUR обновлён с ЦБ РФ:\n{new_rate:.4f} ₽ за 1 EUR")
                except Exception as e:
                    conn.rollback()
                    messagebox.showerror("Ошибка", f"Не удалось сохранить курс: {e}")
                finally:
                    conn.close()
        else:
            messagebox.showwarning("Не обновлено",
                                   "Не удалось получить курс EUR с ЦБ РФ.\n"
                                   "Проверьте подключение к интернету.\n\n"
                                    "Будет использован старый курс.")

    def _refresh_cny_rate(self):
        """Обновление курса CNY через ЦБ РФ."""
        rates = fetch_cbr_exchange_rates()

        if rates and "CNY" in rates:
            new_rate = rates["CNY"]
            self.cny_rub_rate_var.set(str(round(new_rate, 4)))
            today = datetime.now().strftime("%Y-%m-%d")
            self.last_update_date_var.set(today)

            conn = get_connection()
            cursor = conn.cursor()
            with _write_lock:
                try:
                    cursor.execute("""
                        INSERT INTO settings (setting_key, setting_value, updated_at)
                        VALUES ('cny_rub_rate', ?, ?)
                        ON CONFLICT(setting_key) DO UPDATE SET setting_value = ?, updated_at = ?
                    """, (str(round(new_rate, 4)), today, str(round(new_rate, 4)), today))
                    conn.commit()
                    messagebox.showinfo("Успех", f"Курс CNY обновлён с ЦБ РФ:\n{new_rate:.4f} ₽ за 1 CNY")
                except Exception as e:
                    conn.rollback()
                    messagebox.showerror("Ошибка", f"Не удалось сохранить курс: {e}")
                finally:
                    conn.close()
        else:
            messagebox.showwarning("Не обновлено",
                                   "Не удалось получить курс CNY с ЦБ РФ.\n"
                                   "Проверьте подключение к интернету.\n\n"
                                   "Будет использован старый курс.")

    def _refresh_all_rates(self):
        """Обновление всех курсов через ЦБ РФ одним запросом."""
        self._usd_status_var.set("Загрузка...")
        self.update()

        rates = fetch_cbr_exchange_rates()
        if not rates:
            self._usd_status_var.set("✗ Ошибка")
            messagebox.showwarning(
                "Не обновлено",
                "Не удалось получить курсы с ЦБ РФ.\n"
                "Проверьте подключение к интернету.\n\n"
                "Будет использован старый курс."
            )
            return

        today = datetime.now().strftime("%Y-%m-%d")
        updated = []

        mapping = [
            ("usd_rub_rate", "USD", self.usd_rub_rate_var),
            ("eur_rub_rate", "EUR", self.eur_rub_rate_var),
            ("cny_rub_rate", "CNY", self.cny_rub_rate_var),
        ]

        with _write_lock:
            conn = get_connection()
            cursor = conn.cursor()
            try:
                for key, currency, var in mapping:
                    if currency in rates:
                        new_rate = round(rates[currency], 4)
                        var.set(str(new_rate))
                        cursor.execute("""
                            INSERT INTO settings (setting_key, setting_value, updated_at)
                            VALUES (?, ?, ?)
                            ON CONFLICT(setting_key) DO UPDATE SET setting_value = ?, updated_at = ?
                        """, (key, str(new_rate), today, str(new_rate), today))
                        updated.append(f"{currency}: {new_rate:.4f} ₽")
                conn.commit()
                self.last_update_date_var.set(today)
                self._usd_status_var.set("✓ Обновлено")
                messagebox.showinfo("Успех", "Курсы обновлены с ЦБ РФ:\n" + "\n".join(updated))
            except Exception as e:
                conn.rollback()
                self._usd_status_var.set("✗ Ошибка")
                messagebox.showerror("Ошибка", f"Не удалось сохранить курсы: {e}")
            finally:
                conn.close()

        # Вне _write_lock: точка истории за текущий месяц + перерисовка графика
        if updated:
            self._record_rate_history(rates)

    def _save_all_rates(self):
        """Сохранение всех курсов (ручной ввод)."""
        mapping = [
            ("usd_rub_rate", "USD", self.usd_rub_rate_var),
            ("eur_rub_rate", "EUR", self.eur_rub_rate_var),
            ("cny_rub_rate", "CNY", self.cny_rub_rate_var),
        ]

        # Валидация до захвата блокировки и соединения
        validated = []
        for key, currency, var in mapping:
            try:
                rate = float(var.get())
                if rate <= 0:
                    messagebox.showerror("Ошибка", f"Курс {currency} должен быть положительным числом")
                    return
            except ValueError:
                messagebox.showerror("Ошибка", f"Введите корректный курс для {currency}")
                return
            validated.append((key, currency, rate))

        # Лимит просадки — тоже валидируем заранее
        try:
            dd_val = float(self.drawdown_limit_var.get())
            if not (0 <= dd_val <= 100):
                messagebox.showerror("Ошибка", "Лимит просадки должен быть от 0 до 100")
                return
        except ValueError:
            messagebox.showerror("Ошибка", "Введите корректный лимит просадки")
            return

        today = datetime.now().strftime("%Y-%m-%d")
        with _write_lock:
            conn = get_connection()
            cursor = conn.cursor()
            try:
                saved = []
                for key, currency, rate in validated:
                    cursor.execute("""
                        INSERT INTO settings (setting_key, setting_value, updated_at)
                        VALUES (?, ?, ?)
                        ON CONFLICT(setting_key) DO UPDATE SET setting_value = ?, updated_at = ?
                    """, (key, str(rate), today, str(rate), today))
                    saved.append(f"{currency}: {rate} ₽")
                conn.commit()
            except Exception as e:
                conn.rollback()
                messagebox.showerror("Ошибка", f"Не удалось сохранить курсы: {e}")
                return
            finally:
                conn.close()

        # Вне _write_lock и соединения: set_drawdown_limit открывает собственное
        # соединение и пишет в ту же таблицу settings. Внутри блокировки это
        # приводило к двум незакоммиченным write-транзакциям и "database is locked".
        set_drawdown_limit(dd_val)
        self.last_update_date_var.set(today)

        # Точка истории за текущий месяц + перерисовка графика
        self._record_rate_history({currency: rate for _, currency, rate in validated})

        messagebox.showinfo("Сохранено", "Курсы сохранены:\n" + "\n".join(saved))

    def _save_usd_rate(self):
        """Сохранение курса USD (ручной ввод)."""
        self._save_rate("usd_rub_rate", "USD")

    def _save_eur_rate(self):
        """Сохранение курса EUR (ручной ввод)."""
        self._save_rate("eur_rub_rate", "EUR")

    def _save_cny_rate(self):
        """Сохранение курса CNY (ручной ввод)."""
        self._save_rate("cny_rub_rate", "CNY")

    def _save_rate(self, setting_key, currency_name):
        """Сохранение курса в БД (ручной ввод)."""
        if setting_key == "usd_rub_rate":
            var = self.usd_rub_rate_var
        elif setting_key == "eur_rub_rate":
            var = self.eur_rub_rate_var
        else:
            var = self.cny_rub_rate_var

        try:
            rate = float(var.get())
            if rate <= 0:
                messagebox.showerror("Ошибка", "Курс должен быть положительным числом")
                return
        except ValueError:
            messagebox.showerror("Ошибка", "Введите корректный курс")
            return

        today = datetime.now().strftime("%Y-%m-%d")
        saved = False
        with _write_lock:
            conn = get_connection()
            cursor = conn.cursor()

            try:
                cursor.execute("""
                    INSERT INTO settings (setting_key, setting_value, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(setting_key) DO UPDATE SET setting_value = ?, updated_at = ?
                """, (setting_key, str(rate), today, str(rate), today))
                conn.commit()
                self.last_update_date_var.set(today)
                saved = True
                messagebox.showinfo("Успех", f"Курс {currency_name} сохранён: {rate} ₽ за 1 {currency_name}")
            except Exception as e:
                conn.rollback()
                messagebox.showerror("Ошибка", f"Не удалось сохранить курс: {e}")
            finally:
                conn.close()

        if saved:
            self._record_rate_history({currency_name: rate})


class StorageSettingsTab(tb.Frame):
    """Вкладка «Хранилище»: расположение БД, архивация, импорт, сопоставление тикеров."""

    def __init__(self, parent, settings_view):
        super().__init__(parent)
        self.settings_view = settings_view
        self._broker_map = {}
        self._create_ui()
        self._populate_brokers()
        self._populate_years()

    def _create_ui(self):
        """Каркас вкладки: область с вертикальной прокруткой + панели."""
        self._canvas = tk.Canvas(self, highlightthickness=0)
        try:
            self._canvas.configure(background=tb.Style().colors.bg)
        except Exception:
            pass
        self._vsb = tb.Scrollbar(self, orient=tk.VERTICAL, command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=self._vsb.set)

        self.scroll_body = tb.Frame(self._canvas)
        self._canvas_window = self._canvas.create_window(
            (0, 0), window=self.scroll_body, anchor='nw'
        )

        self._canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._vsb.pack(side=tk.RIGHT, fill=tk.Y)

        self.scroll_body.bind('<Configure>', self._on_body_configure)
        self._canvas.bind('<Configure>', self._on_canvas_configure)
        self.bind_all('<MouseWheel>', self._on_mousewheel)

        self._create_panels()

    def destroy(self):
        try:
            self.unbind_all('<MouseWheel>')
        except tk.TclError:
            pass
        super().destroy()

    def _on_body_configure(self, _event):
        """Обновить scrollregion и показать/скрыть скроллбар по размеру контента."""
        self._canvas.configure(scrollregion=self._canvas.bbox('all'))
        bbox = self._canvas.bbox('all')
        if bbox and bbox[3] > self._canvas.winfo_height():
            self._vsb.pack(side=tk.RIGHT, fill=tk.Y)
        else:
            self._vsb.pack_forget()

    def _on_canvas_configure(self, event):
        """Растянуть контент на ширину canvas (без горизонтальной прокрутки)."""
        self._canvas.itemconfigure(self._canvas_window, width=event.width)

    def _on_mousewheel(self, event):
        """Прокрутка, только если указатель над вкладкой и есть что прокручивать."""
        if not self.winfo_exists() or not self._canvas.winfo_exists():
            return
        if self._canvas.yview() == (0.0, 1.0):
            return
        widget = self.winfo_containing(event.x_root, event.y_root)
        while widget is not None:
            if widget is self._canvas or widget is self.scroll_body:
                self._canvas.yview_scroll(int(-1 * (event.delta / 120)), 'units')
                return
            widget = widget.master

    def _create_panels(self):
        # ─── Импорт ───
        import_frame = tb.LabelFrame(self.scroll_body, text="Импорт", padx=10, pady=10)
        import_frame.pack(fill=tk.X, padx=5, pady=5)

        # --- Стоимость активов ---
        asset_frame = tb.LabelFrame(import_frame, text="Стоимость активов", padx=10, pady=10)
        asset_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)

        row = 0
        tb.Label(asset_frame, text="Брокер:").grid(row=row, column=0, sticky=tk.W, padx=5, pady=5)
        self.asset_broker_var = tk.StringVar()
        self.asset_broker_combo = tb.Combobox(
            asset_frame, textvariable=self.asset_broker_var, width=20, state="readonly"
        )
        self.asset_broker_combo.grid(row=row, column=1, sticky=tk.W, padx=5, pady=5)
        row += 1

        tb.Label(asset_frame, text="Год:").grid(row=row, column=0, sticky=tk.W, padx=5, pady=5)
        self.asset_year_var = tk.StringVar()
        self.asset_year_combo = tb.Combobox(asset_frame, textvariable=self.asset_year_var, width=10, state="readonly")
        self.asset_year_combo.grid(row=row, column=1, sticky=tk.W, padx=5, pady=5)
        tb.Button(
            asset_frame, text="Импорт срез", command=self._import_asset_values, bootstyle="primary"
        ).grid(row=row, column=2, padx=5, pady=5)
        row += 1

        tb.Label(asset_frame, text="Выберите файл:").grid(row=row, column=0, sticky=tk.W, padx=5, pady=5)
        self.asset_file_var = tk.StringVar()
        tb.Entry(asset_frame, textvariable=self.asset_file_var, width=30).grid(row=row, column=1, sticky=tk.W, padx=5, pady=5)
        tb.Button(asset_frame, text="Обзор", command=self._browse_asset_file).grid(row=row, column=2, padx=5, pady=5)

        # --- Доходы ---
        income_frame = tb.LabelFrame(import_frame, text="Доходы", padx=10, pady=10)
        income_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)

        row = 0
        tb.Label(income_frame, text="Брокер:").grid(row=row, column=0, sticky=tk.W, padx=5, pady=5)
        self.income_broker_var = tk.StringVar()
        self.income_broker_combo = tb.Combobox(
            income_frame, textvariable=self.income_broker_var, width=20, state="readonly"
        )
        self.income_broker_combo.grid(row=row, column=1, sticky=tk.W, padx=5, pady=5)
        row += 1

        tb.Label(income_frame, text="Год:").grid(row=row, column=0, sticky=tk.W, padx=5, pady=5)
        self.income_year_var = tk.StringVar()
        self.income_year_combo = tb.Combobox(income_frame, textvariable=self.income_year_var, width=10, state="readonly")
        self.income_year_combo.grid(row=row, column=1, sticky=tk.W, padx=5, pady=5)
        tb.Button(
            income_frame, text="Импорт доходов", command=self._import_incomes, bootstyle="primary"
        ).grid(row=row, column=2, padx=5, pady=5)
        row += 1

        tb.Label(income_frame, text="Выберите файл:").grid(row=row, column=0, sticky=tk.W, padx=5, pady=5)
        self.income_file_var = tk.StringVar()
        tb.Entry(income_frame, textvariable=self.income_file_var, width=30).grid(row=row, column=1, sticky=tk.W, padx=5, pady=5)
        tb.Button(income_frame, text="Обзор", command=self._browse_income_file).grid(row=row, column=2, padx=5, pady=5)

        # ─── Импорт заявок QUIK ───
        quik_frame = tb.LabelFrame(self.scroll_body, text="Импорт заявок QUIK", padx=10, pady=10)
        quik_frame.pack(fill=tk.X, padx=5, pady=5)

        tb.Label(
            quik_frame,
            text=(
                "Исполненные заявки из XLSX-экспорта QUIK пишутся в транзакции; "
                "активы и баланс счёта меняются как при обычных сделках "
                "(отключается галочкой в окне импорта).\n"
                "Новые тикеры попадают в реестр (тип — по секции биржи). "
                "Дата задаётся в окне; повторный импорт дубли не создаёт."
            ),
            foreground="gray", justify=tk.LEFT, wraplength=520
        ).grid(row=0, column=0, columnspan=3, sticky=tk.W, padx=5, pady=(0, 5))

        tb.Label(quik_frame, text="Брокер:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=5)
        self.quik_broker_var = tk.StringVar()
        self.quik_broker_combo = tb.Combobox(
            quik_frame, textvariable=self.quik_broker_var, width=20, state="readonly"
        )
        self.quik_broker_combo.grid(row=1, column=1, sticky=tk.W, padx=5, pady=5)
        tb.Button(
            quik_frame, text="Выбрать файл…", command=self._import_quik_orders, bootstyle="primary"
        ).grid(row=1, column=2, padx=5, pady=5)

        # ─── Архивация ───
        arch_frame = tb.LabelFrame(self.scroll_body, text="Архивация", padx=10, pady=10)
        arch_frame.pack(fill=tk.X, padx=5, pady=5)

        from app_config import get_archive_settings, get_default_archive_folder
        archive_settings = get_archive_settings()
        self.archive_enabled_var = tk.BooleanVar(value=archive_settings["enabled"])
        tb.Checkbutton(arch_frame, text="Архивировать при закрытии", variable=self.archive_enabled_var).grid(row=0, column=0, columnspan=3, sticky=tk.W, padx=5, pady=5)

        row = 1
        tb.Label(arch_frame, text="Количество архивов:").grid(row=row, column=0, sticky=tk.W, padx=5, pady=5)
        self.archive_count_var = tk.IntVar(value=archive_settings["count"])
        tb.Spinbox(arch_frame, from_=1, to=99, textvariable=self.archive_count_var, width=5).grid(row=row, column=1, sticky=tk.W, padx=5, pady=5)
        row += 1

        tb.Label(arch_frame, text="Папка для архивов:").grid(row=row, column=0, sticky=tk.W, padx=5, pady=5)
        default_folder = archive_settings["folder"] or get_default_archive_folder()
        self.archive_folder_var = tk.StringVar(value=default_folder)
        tb.Entry(arch_frame, textvariable=self.archive_folder_var, width=30).grid(row=row, column=1, sticky=tk.W, padx=5, pady=5)
        tb.Button(arch_frame, text="Обзор", command=self._browse_archive_folder).grid(row=row, column=2, padx=5, pady=5)
        row += 1

        tb.Button(arch_frame, text="Сохранить", command=self._save_archive_settings, bootstyle="success").grid(row=row, column=0, columnspan=3, pady=5)

        # ─── Хранилище (база данных) ───
        db_frame = tb.LabelFrame(self.scroll_body, text="Хранилище", padx=10, pady=10)
        db_frame.pack(fill=tk.X, padx=5, pady=5)

        tb.Label(db_frame, text="Путь к базе данных:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=5)
        self.db_path_var = tk.StringVar(value=get_db_path())
        tb.Entry(db_frame, textvariable=self.db_path_var, width=45, state='readonly').grid(row=0, column=1, sticky=tk.W, padx=5, pady=5)
        tb.Button(db_frame, text="Обзор…", command=self._browse_db, bootstyle="primary").grid(row=0, column=2, padx=5, pady=5)
        tb.Button(db_frame, text="Сохранить как… (копия)", command=self._backup_db, bootstyle="success").grid(row=1, column=0, columnspan=3, pady=5)

        # ─── Сопоставление тикеров ───
        match_frame = tb.LabelFrame(self.scroll_body, text="Сопоставление тикеров", padx=10, pady=10)
        match_frame.pack(fill=tk.X, padx=5, pady=5)

        tb.Label(
            match_frame,
            text=(
                "После импорта CSV строки без тикера получают техническое имя "
                "(АКТИВ_1, АКТИВ_2 …).\n"
                "Кнопка сопоставит их с реестром тикеров по названию. "
                "Нужно только один раз после импорта."
            ),
            foreground="gray", justify=tk.LEFT
        ).grid(row=0, column=0, columnspan=2, sticky=tk.W, padx=5, pady=5)

        tb.Button(
            match_frame, text="🔗 Сопоставить АКТИВ_* с реестром",
            command=self._convert_placeholder_tickers, bootstyle="primary"
        ).grid(row=1, column=0, padx=5, pady=5, sticky=tk.W)

    def _browse_db(self):
        """Выбрать существующую БД."""
        path = filedialog.askopenfilename(
            title="Выберите файл базы данных",
            filetypes=[("SQLite база данных", "*.db"), ("Все файлы", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, 'rb') as f:
                header = f.read(16)
        except OSError:
            messagebox.showerror("Ошибка", "Не удалось прочитать выбранный файл.")
            return
        if header != b"SQLite format 3\x00":
            messagebox.showerror("Ошибка", "Выбранный файл не является базой данных SQLite.")
            return
        from app_config import set_db_path as cfg_set
        cfg_set(path)
        self.db_path_var.set(path)
        messagebox.showinfo("Смена базы данных", "База данных изменена. Программа будет перезапущена.")
        self.settings_view.controller.relaunch_app()

    def _backup_db(self):
        """Сохранить копию текущей БД."""
        default_name = f"investments_backup_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.db"
        dest = filedialog.asksaveasfilename(
            title="Сохранить копию базы данных",
            initialfile=default_name,
            defaultextension=".db",
            filetypes=[("SQLite база данных", "*.db"), ("Все файлы", "*.*")],
        )
        if not dest:
            return
        try:
            backup_database(dest)
            messagebox.showinfo("Успех", f"Копия базы данных сохранена:\n{dest}")
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось сохранить копию:\n{e}")

    def _browse_archive_folder(self):
        """Выбрать папку для архивов."""
        folder = filedialog.askdirectory(title="Выберите папку для архивов")
        if folder:
            self.archive_folder_var.set(folder)

    def _save_archive_settings(self):
        """Сохранить настройки архивации."""
        from app_config import set_archive_settings
        set_archive_settings(
            self.archive_enabled_var.get(),
            int(self.archive_count_var.get()),
            self.archive_folder_var.get(),
        )
        messagebox.showinfo("Сохранено", "Настройки архивации сохранены.")

    def _populate_brokers(self):
        """Заполнить комбобокс брокеров."""
        self._broker_map = {}
        accounts = get_all_accounts()
        values = []
        for acc in accounts:
            display = acc['name']
            if acc['account_number']:
                display += f" · {acc['account_number']}"
            values.append(display)
            self._broker_map[display] = acc['id']
        self.asset_broker_combo['values'] = values
        self.income_broker_combo['values'] = values
        self.quik_broker_combo['values'] = values

    def _populate_years(self):
        """Заполнить комбобоксы года из БД."""
        current_year = str(datetime.now().year)
        min_year = 2015
        max_year = datetime.now().year + 1
        generated = [str(y) for y in range(max_year, min_year - 1, -1)]
        asset_years = get_snapshot_years()
        all_years = list(dict.fromkeys(generated + asset_years))
        self.asset_year_combo['values'] = all_years
        self.asset_year_var.set(all_years[0])

        income_tx_years = get_transaction_years()
        income_years = list(dict.fromkeys(generated + income_tx_years))
        self.income_year_combo['values'] = income_years
        self.income_year_var.set(income_years[0])

    def _browse_asset_file(self):
        path = filedialog.askopenfilename(filetypes=[("CSV", "*.csv"), ("Все файлы", "*.*")])
        if path:
            self.asset_file_var.set(path)

    def _convert_placeholder_tickers(self):
        """Сопоставить технические тикеры АКТИВ_* с реестром по названию."""
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) AS c FROM snapshot_assets WHERE ticker LIKE 'АКТИВ\\_%' ESCAPE '\\'"
        )
        sa_count = cursor.fetchone()["c"]
        cursor.execute(
            "SELECT COUNT(*) AS c FROM assets WHERE ticker LIKE 'АКТИВ\\_%' ESCAPE '\\'"
        )
        a_count = cursor.fetchone()["c"]
        conn.close()

        total = sa_count + a_count
        if total == 0:
            messagebox.showinfo(
                "Сопоставление",
                "Технических тикеров АКТИВ_* не найдено.\n"
                "Нечего сопоставлять."
            )
            return

        if not messagebox.askyesno(
            "Подтверждение",
            f"Записей с тикером АКТИВ_*: {total}.\n\n"
            "Сопоставить их с реестром тикеров по названию?\n"
            "Рекомендуется сначала сделать резервную копию БД."
        ):
            return

        result = convert_placeholder_tickers()
        converted = result["converted"]
        ambiguous = result["ambiguous"]
        not_found = result["not_found"]

        lines = [f"✓ Сопоставлено: {len(converted)}"]
        for old, new, name in converted[:20]:
            lines.append(f"   {old} → {new}   «{name}»")
        if len(converted) > 20:
            lines.append(f"   …и ещё {len(converted) - 20}")

        if ambiguous:
            lines.append("")
            lines.append(f"⚠ Неоднозначно: {len(ambiguous)}")
            for old, name, tickers in ambiguous[:10]:
                lines.append(f"   {old}   «{name}»: {', '.join(tickers)}")
            if len(ambiguous) > 10:
                lines.append(f"   …и ещё {len(ambiguous) - 10}")

        if not_found:
            lines.append("")
            lines.append(f"✗ Не найдено в реестре: {len(not_found)}")
            for old, name in not_found[:10]:
                label = name if name else "(без названия)"
                lines.append(f"   {old}   «{label}»")
            if len(not_found) > 10:
                lines.append(f"   …и ещё {len(not_found) - 10}")

        messagebox.showinfo("Отчёт о сопоставлении", "\n".join(lines))
        self.settings_view.tickers_tab.refresh()

    def _import_asset_values(self):
        """Импорт исторических срезов стоимости активов из CSV."""
        broker_display = self.asset_broker_var.get()
        year = self.asset_year_var.get()
        filepath = self.asset_file_var.get()

        if not broker_display:
            messagebox.showwarning("Брокер не выбран", "Выберите брокера из списка.")
            return
        if not year:
            messagebox.showwarning("Год не выбран", "Выберите год.")
            return
        if not filepath:
            messagebox.showwarning("Файл не выбран", "Выберите CSV-файл.")
            return

        encoding = _detect_encoding(filepath)
        try:
            with open(filepath, 'r', encoding=encoding) as f:
                lines = f.readlines()
        except Exception as e:
            messagebox.showerror("Ошибка чтения файла", f"Не удалось прочитать файл:\n{e}")
            return

        balance_row = None
        deposit_row = None
        asset_rows = []
        counter = 1
        for line in lines:
            line = line.strip()
            if not line:
                continue
            parts = line.split(';')
            if len(parts) < 4:
                continue
            if _is_header_row(parts):
                continue

            name = parts[0].strip()
            ticker = parts[1].strip()
            asset_type = parts[2].strip().lower()
            currency_code = parts[3].strip() or 'RUB'

            if asset_type == 'баланс':
                if balance_row is None:
                    mv = {}
                    for i in range(4, 16):
                        if i < len(parts):
                            val = _parse_number(parts[i])
                            if val is not None and val != 0:
                                mv[i - 3] = val
                    if mv:
                        balance_row = {'currency_code': currency_code, 'month_values': mv}
                continue

            if asset_type == 'пополнил':
                if deposit_row is None:
                    mv = {}
                    for i in range(4, 16):
                        if i < len(parts):
                            val = _parse_number(parts[i])
                            if val is not None and val != 0:
                                mv[i - 3] = val
                    if mv:
                        deposit_row = {'currency_code': currency_code, 'month_values': mv}
                continue

            if not ticker:
                ticker = f"АКТИВ_{counter}"
                counter += 1

            month_values = {}
            for i in range(4, 16):
                if i < len(parts):
                    val = _parse_number(parts[i])
                    if val is not None and val != 0:
                        month_values[i - 3] = val

            asset_rows.append({
                'name': name,
                'ticker': ticker,
                'asset_type': asset_type,
                'currency_code': currency_code,
                'month_values': month_values,
            })

        if not asset_rows:
            messagebox.showinfo("Импорт", "Файл не содержит данных для импорта.")
            return

        broker_id = self._broker_map.get(broker_display)
        if not broker_id:
            messagebox.showerror("Ошибка", "Не удалось определить ID брокера.")
            return

        result = import_asset_slices(broker_id, int(year), asset_rows, balance_row, deposit_row)

        if result[0] == -1:
            conflict_months = ', '.join(result[1])
            messagebox.showwarning(
                "Срезы уже существуют",
                f"За следующие месяцы уже есть срезы:\n{conflict_months}\n\n"
                "Импорт отменён. Удалите существующие срезы и попробуйте снова."
            )
            return

        months, slices = result
        total_assets = len(asset_rows)

        new_tickers = 0
        for row in asset_rows:
            ticker = row['ticker'].strip()
            name = row['name'].strip() if row['name'] else ''
            if not ticker or ticker.startswith('АКТИВ_'):
                continue
            existing = get_ticker_name(ticker)
            if existing is None:
                try:
                    add_ticker_name(ticker, name)
                    new_tickers += 1
                except ValueError:
                    pass
            elif not existing and name:
                update_ticker_name(ticker, name)

        balance_info = ""
        if balance_row and balance_row.get('month_values'):
            balance_info = f"\nБаланс: {len(balance_row['month_values'])} мес."
        deposit_info = ""
        if deposit_row and deposit_row.get('month_values'):
            deposit_info = f"\nПополнения/выводы: {len(deposit_row['month_values'])} мес."
        ticker_info = f"\nТикеров добавлено: {new_tickers}" if new_tickers > 0 else ""
        messagebox.showinfo(
            "Импорт завершён",
            f"Импортировано:\n"
            f"Активов: {total_assets}\n"
            f"Срезов: {slices}\n"
            f"Месяцев: {months}{balance_info}{deposit_info}{ticker_info}"
        )
        self._populate_years()
        self.settings_view.tickers_tab.refresh()

    def _browse_income_file(self):
        path = filedialog.askopenfilename(filetypes=[("CSV", "*.csv")])
        if path:
            self.income_file_var.set(path)

    def _import_quik_orders(self):
        """Импорт заявок QUIK из XLSX в transactions (окно предпросмотра)."""
        from quik_import import parse_quik_xlsx, QuikImportDialog
        path = filedialog.askopenfilename(
            title="Выберите XLSX-экспорт заявок QUIK",
            filetypes=[("Excel", "*.xlsx"), ("Все файлы", "*.*")],
        )
        if not path:
            return
        try:
            orders = parse_quik_xlsx(path)
        except ValueError as e:
            messagebox.showerror("Ошибка формата", str(e))
            return
        except Exception as e:
            messagebox.showerror("Ошибка чтения файла", f"Не удалось прочитать файл:\n{e}")
            return
        if not orders:
            messagebox.showinfo("Импорт", "В файле не найдено заявок (покупка/продажа).")
            return
        accounts = get_all_accounts()
        if not accounts:
            messagebox.showwarning("Нет счетов", "Сначала создайте счёт на вкладке «Счета».")
            return

        default_account_id = self._broker_map.get(self.quik_broker_var.get())

        def on_done(imported, skipped, new_tickers, stats=None):
            msg = f"Импортировано транзакций: {imported}"
            if skipped:
                msg += f"\nПропущено дубликатов: {skipped}"
            if new_tickers:
                msg += f"\nНовых тикеров в реестре: {new_tickers}"
            if stats:
                if stats.get('created'):
                    msg += (f"\nНовых позиций: {len(stats['created'])} "
                            f"({', '.join(dict.fromkeys(stats['created']))})")
                if stats.get('bought'):
                    msg += (f"\nДокуплено позиций: {len(stats['bought'])} "
                            f"({', '.join(dict.fromkeys(stats['bought']))})")
                if stats.get('sold'):
                    msg += (f"\nПродано позиций: {len(stats['sold'])} "
                            f"({', '.join(dict.fromkeys(stats['sold']))})")
                for w in stats.get('warnings', []):
                    msg += f"\nВнимание: {w}"
            messagebox.showinfo("Импорт заявок завершён", msg)

        QuikImportDialog(self, orders, accounts,
                         default_account_id=default_account_id, on_done=on_done)

    def _import_incomes(self):
        """Импорт доходов (купоны, дивиденды) из CSV."""
        broker_display = self.income_broker_var.get()
        year = self.income_year_var.get()
        filepath = self.income_file_var.get()

        if not broker_display:
            messagebox.showwarning("Брокер не выбран", "Выберите брокера из списка.")
            return
        if not year:
            messagebox.showwarning("Год не выбран", "Выберите год.")
            return
        if not filepath:
            messagebox.showwarning("Файл не выбран", "Выберите CSV-файл.")
            return

        encoding = _detect_encoding(filepath)
        try:
            with open(filepath, 'r', encoding=encoding) as f:
                lines = f.readlines()
        except Exception as e:
            messagebox.showerror("Ошибка чтения файла", f"Не удалось прочитать файл:\n{e}")
            return

        income_rows = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            parts = line.split(';')
            if len(parts) < 4:
                continue
            if parts[0].strip().lower() == 'актив' and 'тип' in parts[2].strip().lower():
                continue

            name = parts[0].strip()
            ticker = parts[1].strip()
            income_type = parts[2].strip().lower()
            currency_code = parts[3].strip() or 'RUB'

            if income_type not in ('купон', 'дивиденд'):
                continue

            month_values = {}
            for i in range(4, 16):
                if i < len(parts):
                    val = _parse_number(parts[i])
                    if val is not None and val != 0:
                        month_values[i - 3] = val

            if month_values:
                income_rows.append({
                    'name': name,
                    'ticker': ticker,
                    'income_type': income_type,
                    'currency_code': currency_code,
                    'month_values': month_values,
                })

        if not income_rows:
            messagebox.showinfo("Импорт", "Файл не содержит данных для импорта.")
            return

        broker_id = self._broker_map.get(broker_display)
        if not broker_id:
            messagebox.showerror("Ошибка", "Не удалось определить ID брокера.")
            return

        created, without_asset = import_incomes(broker_id, int(year), income_rows)

        if created == 0:
            messagebox.showinfo("Импорт", "Нет данных для импорта (все значения = 0).")
            return

        msg = f"Импортировано: {created} записей"
        if without_asset > 0:
            msg += f"\n{without_asset} без актива (нет в портфеле)"
        messagebox.showinfo("Импорт завершён", msg)
        self._populate_years()


# ═══════════════════════════════════════════════════════════
#  Вкладка «Реестр тикеров"  
# ═══════════════════════════════════════════════════════════

_TYPE_MAP = {'акция': 'Акция', 'облигация': 'Облигация', 'etf': 'ETF'}
_TYPE_CHOICES = ['акция', 'облигация', 'etf']
_CURRENCY_CHOICES = ['RUB', 'USD', 'EUR', 'CNY']


def _ui_type(key):
    """Преобразовать хранящийся тип в отображаемый."""
    return _TYPE_MAP.get(key, key) if key else ''


def _db_type(ui_key):
    """Преобразовать отображаемый тип в хранящийся."""
    return ui_key.lower().strip() if ui_key else ''


class TickerRegistryTab(tb.Frame):
    """Вкладка управления реестром тикеров."""

    def __init__(self, parent):
        super().__init__(parent)
        self._creating_ui = True
        self._create_ui()
        self._creating_ui = False
        self.refresh()

    def _create_ui(self):
        # Поиск
        search_frame = tb.Frame(self)
        search_frame.pack(fill=tk.X, padx=10, pady=(5, 5))

        tb.Label(search_frame, text="Поиск:").pack(side=tk.LEFT, padx=(0, 4))
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._on_search())
        search_entry = tb.Entry(search_frame, textvariable=self.search_var, width=30)
        search_entry.pack(side=tk.LEFT, padx=(0, 5))
        _bind_entry_context_menu(search_entry)

        # Статус (для синхронизации)
        self._sync_status_var = tk.StringVar(value="")
        tb.Label(search_frame, textvariable=self._sync_status_var, foreground="gray").pack(side=tk.RIGHT, padx=(5, 0))

        # Кнопки
        btn_frame = tb.Frame(self)
        btn_frame.pack(fill=tk.X, padx=10, pady=(2, 5))
        tb.Button(btn_frame, text="Добавить", command=self._add_ticker, bootstyle="success").pack(side=tk.LEFT, padx=2)
        tb.Button(btn_frame, text="Редактировать", command=self._edit_ticker, bootstyle="info").pack(side=tk.LEFT, padx=2)
        tb.Button(btn_frame, text="Удалить", command=self._delete_ticker, bootstyle="danger").pack(side=tk.LEFT, padx=2)
        tb.Button(btn_frame, text="🔄 Обновить с биржи", command=self._sync_from_moex, bootstyle="warning").pack(side=tk.LEFT, padx=2)
        tb.Button(btn_frame, text="⬆ Импорт", command=self._import_tickers, bootstyle="secondary").pack(side=tk.LEFT, padx=2)
        tb.Button(btn_frame, text="⬇ Экспорт", command=self._export_tickers, bootstyle="secondary").pack(side=tk.LEFT, padx=2)

        # Таблица
        table_frame = tb.LabelFrame(self, text="Реестр", padx=5, pady=5)
        table_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        columns = ('ticker', 'name', 'type', 'lot_size', 'currency')
        self.tree = tb.Treeview(table_frame, columns=columns, show='headings')

        self.tree.heading('ticker', text='Тикер')
        self.tree.heading('name', text='Название')
        self.tree.heading('type', text='Тип')
        self.tree.heading('lot_size', text='Лотность')
        self.tree.heading('currency', text='Валюта')

        self.tree.column('ticker', width=140)
        self.tree.column('name', width=200)
        self.tree.column('type', width=70)
        self.tree.column('lot_size', width=60)
        self.tree.column('currency', width=60)

        scrollbar = tb.Scrollbar(
            table_frame, orient=tk.VERTICAL, command=self.tree.yview,
        )
        self.tree.configure(yscroll=scrollbar.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Двойной клик для редактирования
        self.tree.bind('<Double-1>', lambda e: self._edit_ticker())

    def _on_search(self):
        """Перезапустить обновление с debounce для поиска."""
        if hasattr(self, '_search_timer'):
            self.after_cancel(self._search_timer)
        self._search_timer = self.after(300, self.refresh)

    def refresh(self):
        """Обновить таблицу."""
        try:
            for item in self.tree.get_children():
                self.tree.delete(item)
        except Exception:
            pass

        search = self.search_var.get().strip().lower()
        rows = get_all_ticker_names()

        for ticker, name, asset_type, lot_size, currency in rows:
            if search and search not in ticker.lower() and search not in name.lower() and search not in (asset_type or '').lower():
                continue
            ui_type = _ui_type(asset_type)
            self.tree.insert('', tk.END, values=(ticker, name or '', ui_type, lot_size or '', currency or ''), tags=(ticker,))

    def _add_ticker(self):
        """Добавить новый тикер."""
        dialog = tb.Toplevel(self)
        dialog.title("Добавить тикер")
        dialog.geometry("380x340")
        dialog.transient(self)
        dialog.grab_set()

        row = 0
        tb.Label(dialog, text="Тикер:").grid(row=row, column=0, sticky=tk.W, padx=10, pady=10)
        ticker_var = tk.StringVar()
        ticker_entry = tb.Entry(dialog, textvariable=ticker_var, width=30)
        ticker_entry.grid(row=row, column=1, padx=5, pady=10)
        _bind_entry_context_menu(ticker_entry)
        row += 1

        tb.Label(dialog, text="Название:").grid(row=row, column=0, sticky=tk.W, padx=10, pady=5)
        name_var = tk.StringVar()
        name_entry = tb.Entry(dialog, textvariable=name_var, width=30)
        name_entry.grid(row=row, column=1, padx=5, pady=5)
        _bind_entry_context_menu(name_entry)
        row += 1

        tb.Label(dialog, text="Тип:").grid(row=row, column=0, sticky=tk.W, padx=10, pady=5)
        type_var = tk.StringVar(value='акция')
        type_combo = tb.Combobox(dialog, textvariable=type_var, values=_TYPE_CHOICES, width=27, state="readonly")
        type_combo.grid(row=row, column=1, padx=5, pady=5)
        row += 1

        tb.Label(dialog, text="Лотность:").grid(row=row, column=0, sticky=tk.W, padx=10, pady=5)
        lot_var = tk.StringVar(value="1")
        lot_spin = tb.Spinbox(dialog, from_=1, to=9999, textvariable=lot_var, width=28, format="%d")
        lot_spin.grid(row=row, column=1, padx=5, pady=5)
        row += 1

        tb.Label(dialog, text="Валюта:").grid(row=row, column=0, sticky=tk.W, padx=10, pady=5)
        curr_var = tk.StringVar(value='RUB')
        curr_combo = tb.Combobox(dialog, textvariable=curr_var, values=_CURRENCY_CHOICES, width=27, state="readonly")
        curr_combo.grid(row=row, column=1, padx=5, pady=5)
        row += 1

        def on_ok():
            ticker = ticker_var.get().strip().upper()
            name = name_var.get().strip()
            asset_type = type_var.get()
            lot_size = lot_var.get()
            currency = curr_var.get()
            try:
                lot_size = int(lot_size)
            except ValueError:
                lot_size = 1
            if not ticker:
                messagebox.showwarning("Ошибка", "Введите тикер")
                return
            if not name:
                messagebox.showwarning("Ошибка", "Введите название")
                return
            try:
                add_ticker_name(ticker, name, asset_type=asset_type, lot_size=lot_size, currency=currency)
            except ValueError:
                messagebox.showwarning("Ошибка", f"Тикер {ticker} уже существует")
                return
            dialog.destroy()
            self.refresh()

        btn_frame = tb.Frame(dialog)
        btn_frame.grid(row=row, column=0, columnspan=2, pady=15)
        tb.Button(btn_frame, text="Сохранить", command=on_ok, bootstyle="success").pack(side=tk.LEFT, padx=5)
        tb.Button(btn_frame, text="Отмена", command=dialog.destroy, bootstyle="secondary").pack(side=tk.LEFT, padx=5)

    def _edit_ticker(self):
        """Редактировать выбранный тикер."""
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("Выбор", "Выберите запись для редактирования")
            return

        ticker_key = self.tree.item(sel[0])['tags'][0]
        values = self.tree.item(sel[0])['values']
        old_ticker = str(ticker_key).strip().upper()
        old_name = str(values[1]) if len(values) > 1 else ''
        old_type = str(values[2]) if len(values) > 2 else 'акция'
        old_lot = str(values[3]) if len(values) > 3 else '1'
        old_curr = str(values[4]) if len(values) > 4 else ''

        # Ищем полную информацию в БД (тип может быть в нижнем регистре)
        info = get_ticker_info(old_ticker)
        if info:
            old_type = info.get("asset_type", old_type) or 'акция'
            old_lot = str(info.get("lot_size", 1))
            old_curr = info.get("currency", old_curr) or ''

        dialog = tb.Toplevel(self)
        dialog.title("Редактировать тикер")
        dialog.geometry("380x400")
        dialog.transient(self)
        dialog.grab_set()

        row = 0
        tb.Label(dialog, text="Тикер:").grid(row=row, column=0, sticky=tk.W, padx=10, pady=10)
        ticker_var = tk.StringVar(value=old_ticker)
        ticker_entry = tb.Entry(dialog, textvariable=ticker_var, width=30)
        ticker_entry.grid(row=row, column=1, padx=5, pady=10)
        _bind_entry_context_menu(ticker_entry)
        row += 1

        tb.Label(dialog, text="Название:").grid(row=row, column=0, sticky=tk.W, padx=10, pady=5)
        name_var = tk.StringVar(value=old_name)
        name_entry = tb.Entry(dialog, textvariable=name_var, width=30)
        name_entry.grid(row=row, column=1, padx=5, pady=5)
        _bind_entry_context_menu(name_entry)
        row += 1

        tb.Label(dialog, text="Тип:").grid(row=row, column=0, sticky=tk.W, padx=10, pady=5)
        type_var = tk.StringVar(value=old_type)
        type_combo = tb.Combobox(dialog, textvariable=type_var, values=_TYPE_CHOICES, width=27, state="readonly")
        type_combo.grid(row=row, column=1, padx=5, pady=5)
        row += 1

        tb.Label(dialog, text="Лотность:").grid(row=row, column=0, sticky=tk.W, padx=10, pady=5)
        lot_var = tk.StringVar(value=old_lot)
        lot_spin = tb.Spinbox(dialog, from_=1, to=9999, textvariable=lot_var, width=28, format="%d")
        lot_spin.grid(row=row, column=1, padx=5, pady=5)
        row += 1

        tb.Label(dialog, text="Валюта:").grid(row=row, column=0, sticky=tk.W, padx=10, pady=5)
        curr_var = tk.StringVar(value=old_curr)
        curr_combo = tb.Combobox(dialog, textvariable=curr_var, values=_CURRENCY_CHOICES, width=27, state="readonly")
        curr_combo.grid(row=row, column=1, padx=5, pady=5)
        row += 1

        def on_ok():
            new_ticker = ticker_var.get().strip().upper()
            name = name_var.get().strip()
            asset_type = type_var.get()
            lot_size = lot_var.get()
            currency = curr_var.get()
            try:
                lot_size = int(lot_size)
            except ValueError:
                lot_size = 1
            if not new_ticker:
                messagebox.showwarning("Ошибка", "Введите тикер")
                return
            if not name:
                messagebox.showwarning("Ошибка", "Введите название")
                return
            try:
                rename_ticker(old_ticker, new_ticker, name)
                update_ticker_name(new_ticker, name, lot_size=lot_size, currency=currency, asset_type=asset_type)
            except ValueError as e:
                messagebox.showwarning("Ошибка", str(e))
                return
            dialog.destroy()
            self.refresh()

        btn_frame = tb.Frame(dialog)
        btn_frame.grid(row=row, column=0, columnspan=2, pady=15)
        tb.Button(btn_frame, text="Сохранить", command=on_ok, bootstyle="success").pack(side=tk.LEFT, padx=5)
        tb.Button(btn_frame, text="Отмена", command=dialog.destroy, bootstyle="secondary").pack(side=tk.LEFT, padx=5)

    def _delete_ticker(self):
        """Удалить выбранную запись."""
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("Выбор", "Выберите запись для удаления")
            return

        ticker = str(self.tree.item(sel[0])['tags'][0])

        if messagebox.askyesno("Подтверждение", f"Удалить тикер {ticker} из реестра?"):
            delete_ticker_name(ticker)
            self.refresh()

    def _sync_from_moex(self):
        """Обновить данные тикеров с Мосбиржи."""
        sel = self.tree.selection()
        if sel:
            tickers = [self.tree.item(s)['tags'][0] for s in sel]
        else:
            tickers = [self.tree.item(c)['tags'][0] for c in self.tree.get_children()]

        if not tickers:
            messagebox.showinfo("Информация", "Нет тикеров для обновления.")
            return

        total = len(tickers)
        ok_count = 0
        fail_count = 0
        failed_list = []
        idx = 0

        self._sync_status_var.set("Обновление...")

        def _do_sync():
            nonlocal ok_count, fail_count, idx
            if idx >= total:
                msg = f"Готово: {ok_count} OK, {fail_count} ошибок"
                self._sync_status_var.set(msg)
                self.refresh()
                if failed_list:
                    lines = [f"Не удалось обновить ({len(failed_list)}):"]
                    for t, reason in failed_list:
                        lines.append(f"  {t} — {reason}")
                    messagebox.showwarning("Необновлённые тикеры", "\n".join(lines))
                return

            ticker = tickers[idx]
            idx += 1
            self._sync_status_var.set(f"{ticker} ({idx}/{total})...")

            try:
                data = fetch_ticker_static(ticker)
                if data:
                    update_ticker_from_moex(
                        ticker,
                        shortname=data.get("shortname", ''),
                        currency=data.get("currency", ''),
                        lot_size=data.get("lot_size", 1),
                        asset_type=data.get("asset_type"),
                    )
                    ok_count += 1
                else:
                    fail_count += 1
                    failed_list.append((ticker, "нет данных / снят с торгов"))
            except Exception as e:
                fail_count += 1
                failed_list.append((ticker, str(e)))
                print(f"[sync] Ошибка при {ticker}: {e}")

            # Пауза между запросами к Мосбирже
            if idx < total:
                self.after(350, _do_sync)
            else:
                self.after(350, _do_sync)

        self.after(500, _do_sync)

    def _export_tickers(self):
        """Экспорт тикеров в CSV-файл."""
        filepath = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("Все файлы", "*.*")],
            initialfile=f"tickers_export_{datetime.now().strftime('%Y-%m-%d')}.csv",
        )
        if not filepath:
            return
        rows = get_all_ticker_names()
        if not rows:
            messagebox.showinfo("Экспорт", "Реестр тикеров пуст.")
            return
        try:
            with open(filepath, 'w', encoding='utf-8-sig', newline='') as f:
                f.write('Тикер;Название;Тип;Лотность;Валюта\n')
                for ticker, name, asset_type, lot_size, currency in rows:
                    name = name or ''
                    if ';' in name:
                        name = '"' + name.replace('"', '""') + '"'
                    ui_type = _ui_type(asset_type)
                    f.write(f'{ticker};{name};{ui_type};{lot_size or ""};{currency or ""}\n')
            messagebox.showinfo("Успех", f"Экспортировано {len(rows)} тикеров.\n{filepath}")
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось экспортировать:\n{e}")

    def _import_tickers(self):
        """Импорт тикеров из CSV-файла."""
        filepath = filedialog.askopenfilename(
            filetypes=[("CSV", "*.csv"), ("Все файлы", "*.*")],
        )
        if not filepath:
            return
        encoding = _detect_encoding(filepath)
        try:
            with open(filepath, 'r', encoding=encoding) as f:
                lines = f.readlines()
        except Exception as e:
            messagebox.showerror("Ошибка чтения", f"Не удалось прочитать файл:\n{e}")
            return
        rows = []
        for i, line in enumerate(lines):
            line = line.strip()
            if not line:
                continue
            parts = line.split(';')
            if len(parts) < 1:
                continue
            if i == 0 and parts[0].strip() in ('Тикер', 'ticker', 'TICKER'):
                continue
            ticker = parts[0].strip()
            name = parts[1].strip() if len(parts) > 1 else ''
            # Тип — обратная совместимость: если введена заглавная буква, маппим
            asset_type = ''
            if len(parts) > 2:
                raw = parts[2].strip().lower()
                if raw in _TYPE_CHOICES:
                    asset_type = raw
                elif raw == 'акции' or raw.startswith('акц'):
                    asset_type = 'акция'
                elif raw == 'бонд' or raw.startswith('обл'):
                    asset_type = 'облигация'
                elif raw in ('etf', 'птф', 'зптф', 'бптф', 'фонд'):
                    asset_type = 'etf'
            lot_size = 1
            if len(parts) > 3:
                try:
                    lot_size = int(parts[3].strip())
                except (ValueError, TypeError):
                    pass
            currency = ''
            if len(parts) > 4:
                currency = parts[4].strip().upper()
                if currency not in _CURRENCY_CHOICES:
                    currency = ''
            if not ticker:
                continue
            rows.append((ticker, name, asset_type, lot_size, currency))
        if not rows:
            messagebox.showinfo("Импорт", "Файл не содержит данных для импорта.")
            return
        try:
            count = import_ticker_names(rows)
            messagebox.showinfo("Успех", f"Импортировано {count} тикеров.\n{filepath}")
            self.refresh()
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось импортировать:\n{e}")
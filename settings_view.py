import tkinter as tk
import ttkbootstrap as tb
from tkinter import messagebox, filedialog
from database import (
    get_all_assets, get_connection, get_all_accounts,
    get_exchange_rates, calculate_total_in_rubles,
    get_snapshot_years, get_transaction_years,
    import_asset_slices, import_incomes,
    get_all_ticker_names, import_ticker_names, add_ticker_name, update_ticker_name, delete_ticker_name, rename_ticker, get_ticker_name, get_ticker_info,
    update_ticker_from_moex,
    get_db_path, backup_database,
)
from api_client import fetch_cbr_exchange_rates, fetch_ticker_static
from datetime import datetime


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
    """Окно настроек с вкладками: Валюты, Общие."""

    def __init__(self, parent, controller=None):
        super().__init__(parent)
        self.controller = controller
        self._create_ui()

    def _create_ui(self):
        """Создание интерфейса с вкладками."""
        self.notebook = tb.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Вкладка "Общие"
        self.general_tab = GeneralSettingsTab(self.notebook, self)
        self.notebook.add(self.general_tab, text="Общие")

        # Вкладка "Валюты"
        self.currencies_tab = CurrenciesSettingsTab(self.notebook, self)
        self.notebook.add(self.currencies_tab, text="Валюты")

        # Вкладка "Тикеры"
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

        self._create_ui()
        self._load_rates_from_db()

    def _create_ui(self):
        """Создание интерфейса вкладки валют."""
        # Рамка курса USD/RUB
        usd_rate_frame = tb.LabelFrame(self, text="Курс USD/RUB", padx=10, pady=10)
        usd_rate_frame.pack(fill=tk.X, padx=5, pady=5)

        row = 0

        # Поле ввода курса
        tb.Label(usd_rate_frame, text="Курс:").grid(row=row, column=0, sticky=tk.W, padx=5, pady=5)
        rate_entry = tb.Entry(usd_rate_frame, textvariable=self.usd_rub_rate_var, width=15)
        rate_entry.grid(row=row, column=1, padx=5, pady=5)
        _bind_entry_context_menu(rate_entry)
        tb.Label(usd_rate_frame, text="₽ за 1 USD").grid(row=row, column=2, padx=5, pady=5)
        row += 1

        # Дата обновления и статус
        tb.Label(usd_rate_frame, text="Дата обновления:").grid(row=row, column=0, sticky=tk.W, padx=5, pady=5)
        date_status_frame = tb.Frame(usd_rate_frame)
        date_status_frame.grid(row=row, column=1, columnspan=2, sticky=tk.W, padx=5, pady=5)
        tb.Label(date_status_frame, textvariable=self.last_update_date_var).pack(side=tk.LEFT)
        tb.Label(date_status_frame, textvariable=self._usd_status_var, foreground="gray").pack(side=tk.LEFT, padx=5)
        row += 1

        # Кнопки USD
        usd_btn_frame = tb.Frame(usd_rate_frame)
        usd_btn_frame.grid(row=row, column=0, columnspan=3, pady=5)
        tb.Button(usd_btn_frame, text="🔄 Обновить курс (USD)", command=self._refresh_usd_rate, bootstyle="info").pack(side=tk.LEFT, padx=5)
        tb.Button(usd_btn_frame, text="Сохранить", command=self._save_usd_rate, bootstyle="success").pack(side=tk.LEFT, padx=5)

        # Рамка курса EUR/RUB
        eur_rate_frame = tb.LabelFrame(self, text="Курс EUR/RUB", padx=10, pady=10)
        eur_rate_frame.pack(fill=tk.X, padx=5, pady=5)

        row = 0

        # Поле ввода курса EUR
        tb.Label(eur_rate_frame, text="Курс:").grid(row=row, column=0, sticky=tk.W, padx=5, pady=5)
        eur_rate_entry = tb.Entry(eur_rate_frame, textvariable=self.eur_rub_rate_var, width=15)
        eur_rate_entry.grid(row=row, column=1, padx=5, pady=5)
        _bind_entry_context_menu(eur_rate_entry)
        tb.Label(eur_rate_frame, text="₽ за 1 EUR").grid(row=row, column=2, padx=5, pady=5)
        row += 1

        # Дата обновления EUR (используем ту же дату)
        tb.Label(eur_rate_frame, text="Дата обновления:").grid(row=row, column=0, sticky=tk.W, padx=5, pady=5)
        tb.Label(eur_rate_frame, textvariable=self.last_update_date_var).grid(row=row, column=1, sticky=tk.W, padx=5, pady=5)
        row += 1

        # Кнопки EUR
        eur_btn_frame = tb.Frame(eur_rate_frame)
        eur_btn_frame.grid(row=row, column=0, columnspan=3, pady=5)
        tb.Button(eur_btn_frame, text="🔄 Обновить курс (EUR)", command=self._refresh_eur_rate, bootstyle="info").pack(side=tk.LEFT, padx=5)
        tb.Button(eur_btn_frame, text="Сохранить", command=self._save_eur_rate, bootstyle="success").pack(side=tk.LEFT, padx=5)

        # Рамка курса CNY/RUB
        cny_rate_frame = tb.LabelFrame(self, text="Курс CNY/RUB", padx=10, pady=10)
        cny_rate_frame.pack(fill=tk.X, padx=5, pady=5)

        row = 0

        tb.Label(cny_rate_frame, text="Курс:").grid(row=row, column=0, sticky=tk.W, padx=5, pady=5)
        cny_rate_entry = tb.Entry(cny_rate_frame, textvariable=self.cny_rub_rate_var, width=15)
        cny_rate_entry.grid(row=row, column=1, padx=5, pady=5)
        _bind_entry_context_menu(cny_rate_entry)
        tb.Label(cny_rate_frame, text="₽ за 1 CNY").grid(row=row, column=2, padx=5, pady=5)
        row += 1

        tb.Label(cny_rate_frame, text="Дата обновления:").grid(row=row, column=0, sticky=tk.W, padx=5, pady=5)
        tb.Label(cny_rate_frame, textvariable=self.last_update_date_var).grid(row=row, column=1, sticky=tk.W, padx=5, pady=5)
        row += 1

        cny_btn_frame = tb.Frame(cny_rate_frame)
        cny_btn_frame.grid(row=row, column=0, columnspan=3, pady=5)
        tb.Button(cny_btn_frame, text="🔄 Обновить курс (CNY)", command=self._refresh_cny_rate, bootstyle="info").pack(side=tk.LEFT, padx=5)
        tb.Button(cny_btn_frame, text="Сохранить", command=self._save_cny_rate, bootstyle="success").pack(side=tk.LEFT, padx=5)

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
            messagebox.showinfo("Успех", f"Курс {currency_name} сохранён: {rate} ₽ за 1 {currency_name}")
        except Exception as e:
            conn.rollback()
            messagebox.showerror("Ошибка", f"Не удалось сохранить курс: {e}")
        finally:
            conn.close()


class GeneralSettingsTab(tb.Frame):
    """Вкладка общих настроек."""

    def __init__(self, parent, settings_view):
        super().__init__(parent)
        self.settings_view = settings_view
        self._broker_map = {}
        self._create_ui()
        self._populate_brokers()
        self._populate_years()

    def _create_ui(self):
        """Создание интерфейса вкладки общие настройки."""
        # ─── Импорт ───
        import_frame = tb.LabelFrame(self, text="Импорт", padx=10, pady=10)
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

        # ─── База данных ───
        db_frame = tb.LabelFrame(self, text="База данных", padx=10, pady=10)
        db_frame.pack(fill=tk.X, padx=5, pady=5)

        row = 0

        tb.Label(db_frame, text="Путь к базе данных:").grid(row=row, column=0, sticky=tk.W, padx=5, pady=5)
        self.db_path_var = tk.StringVar(value=get_db_path())
        tb.Entry(db_frame, textvariable=self.db_path_var, width=45, state='readonly').grid(row=row, column=1, sticky=tk.W, padx=5, pady=5)
        tb.Button(db_frame, text="Обзор…", command=self._browse_db, bootstyle="primary").grid(row=row, column=2, padx=5, pady=5)
        row += 1

        tb.Button(db_frame, text="Сохранить как… (копия)", command=self._backup_db, bootstyle="success").grid(row=row, column=0, columnspan=3, pady=5)

        # ─── Архивация ───
        arch_frame = tb.LabelFrame(self, text="Архивация", padx=10, pady=10)
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

    def _import_asset_values(self):
        """Импорт исторических срезов стоимости активов из CSV."""
        broker_display = self.asset_broker_var.get()
        year = self.asset_year_var.get()
        filepath = self.asset_file_var.get()

        if not broker_display:
            messagebox.showwarning("Брокер не выбран", "Выберите брокер из списка.")
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

            # Строка баланса — не создаёт актив, заполняет balance_rub
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

            # Строка пополнений/выводов — заполняет deposits/withdrawals
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

        # Пополнить реестр тикеров: добавить отсутствующие, существующие не перезаписывать
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

    def _import_incomes(self):
        """Импорт доходов (купоны, дивиденды) из CSV."""
        broker_display = self.income_broker_var.get()
        year = self.income_year_var.get()
        filepath = self.income_file_var.get()

        if not broker_display:
            messagebox.showwarning("Брокер не выбран", "Выберите брокер из списка.")
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

    def _browse_db(self):
        """Выбрать существующую БД."""
        from tkinter import filedialog
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
        from tkinter import filedialog
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


# ═══════════════════════════════════════════════════════════
#  Вкладка «Реестр тикеров»
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
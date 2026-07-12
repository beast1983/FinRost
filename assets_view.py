import tkinter as tk
import ttkbootstrap as tb
from tkinter import messagebox
from database import (
    get_all_assets, add_asset, sell_asset, remove_asset, update_asset,
    update_asset_price, get_all_accounts, get_account, get_exchange_rates,
    get_asset, calculate_total_in_rubles, buy_more_asset,
    credit_coupon_or_dividend, get_currencies, get_currency_id,
    get_ticker_name
)
from datetime import datetime
from api_client import fetch_price, is_connected, _fetch_bond_static
from calendar_utils import create_date_entry
from table_utils import apply_zebra


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


class AssetsView(tb.Frame):
    """Вкладка управления активами."""

    def __init__(self, parent, controller=None):
        super().__init__(parent)
        self.controller = controller
        self.current_broker_id = None  # None = все брокеры
        self._create_ui()
        self.refresh()

    def set_broker(self, broker_id):
        """Установить текущего выбранного брокера."""
        self.current_broker_id = broker_id

    def _create_ui(self):
        """Создание интерфейса."""
        # Рамка таблицы
        table_frame = tb.Frame(self)
        table_frame.pack(fill=tk.BOTH, expand=True, pady=5)

        # Treeview для отображения активов
        columns = ('name', 'ticker', 'coupon_percent', 'asset_type', 'list_level', 'quantity', 'lot_value', 'avg_price', 'current_price', 'total_value', 'purchase_date', 'last_update', 'currency')
        self.tree = tb.Treeview(table_frame, columns=columns, show='headings', height=20)

        style = tb.Style()
        style.configure('Assets.Treeview.Heading', font=('Arial', 8, 'bold'))

        # Настройка колонок
        self.tree.heading('name', text='Название бумаги')
        self.tree.heading('ticker', text='Код')
        self.tree.heading('coupon_percent', text='Ставка %')
        self.tree.heading('asset_type', text='Тип')
        self.tree.heading('list_level', text='Уровень')
        self.tree.heading('quantity', text='Количество')
        self.tree.heading('lot_value', text='Стоимость лота')
        self.tree.heading('avg_price', text='Средняя цена')
        self.tree.heading('current_price', text='Текущая цена')
        self.tree.heading('total_value', text='Общая стоимость')
        self.tree.heading('purchase_date', text='Дата покупки')
        self.tree.heading('last_update', text='Последнее обновление')
        self.tree.heading('currency', text='Валюта')

        self.tree.column('name', width=150)
        self.tree.column('ticker', width=70)
        self.tree.column('coupon_percent', width=80, anchor=tk.CENTER)
        self.tree.column('asset_type', width=60)
        self.tree.column('list_level', width=60, anchor=tk.CENTER)
        self.tree.column('quantity', width=80, anchor=tk.CENTER)
        self.tree.column('lot_value', width=90, anchor=tk.E)
        self.tree.column('avg_price', width=80, anchor=tk.E)
        self.tree.column('current_price', width=85, anchor=tk.E)
        self.tree.column('total_value', width=120, anchor=tk.E)
        self.tree.column('purchase_date', width=90, anchor=tk.CENTER)
        self.tree.column('last_update', width=120, anchor=tk.CENTER)
        self.tree.column('currency', width=60, anchor=tk.CENTER)

        self.tree.configure(style='Assets.Treeview')

        # Скроллбар
        scrollbar = tb.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscroll=scrollbar.set)

        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Кнопки управления
        btn_frame = tb.Frame(self)
        btn_frame.pack(fill=tk.X, pady=5)

        tb.Button(btn_frame, text="Купить", command=self._buy_asset, bootstyle="success").pack(side=tk.LEFT, padx=2)
        tb.Button(btn_frame, text="Продать", command=self._sell_asset, bootstyle="warning").pack(side=tk.LEFT, padx=2)
        tb.Button(btn_frame, text="Купон/Дивиденд", command=self._credit, bootstyle="primary").pack(side=tk.LEFT, padx=2)
        tb.Button(btn_frame, text="Изменить", command=self._edit_asset, bootstyle="info").pack(side=tk.LEFT, padx=2)
        tb.Button(btn_frame, text="Обновить цены", command=self._refresh_prices, bootstyle="info").pack(side=tk.LEFT, padx=2)
        tb.Button(btn_frame, text="Сохранить срез", command=self._save_snapshot, bootstyle="primary").pack(side=tk.LEFT, padx=2)
        tb.Button(btn_frame, text="Перечитать", command=self.refresh, bootstyle="info").pack(side=tk.LEFT, padx=2)
        tb.Button(btn_frame, text="Удалить", command=self._remove_asset, bootstyle="danger").pack(side=tk.LEFT, padx=2)

        # Статус бар
        self.status_var = tk.StringVar(value="Готово")
        status_bar = tb.Label(self, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W)
        status_bar.pack(fill=tk.X, side=tk.BOTTOM)

        # Привязываем двойной клик
        self.tree.bind('<Double-1>', lambda e: self._sell_asset())

    def _get_account_name(self, account_id):
        """Получить имя счёта по ID."""
        if account_id is None:
            return "—"
        account = get_account(account_id)
        if not account:
            return "—"
        acct_num = account["account_number"] if "account_number" in account.keys() else ""
        if acct_num:
            return f'{account["name"]} · {acct_num}'
        return account["name"]

    def _get_currency_from_asset(self, asset):
        """Получить валюту актива (currency_code)."""
        if "currency_code" in asset.keys():
            return asset["currency_code"] or "RUB"
        return "RUB"

    def _center_on_parent(self, dialog):
        """Центрировать диалог над родительским окном."""
        dialog.update_idletasks()
        parent = self.master
        x = parent.winfo_x() + (parent.winfo_width() - dialog.winfo_width()) // 2
        y = parent.winfo_y() + (parent.winfo_height() - dialog.winfo_height()) // 2
        dialog.geometry(f"+{x}+{y}")

    def _refresh_prices(self):
        """Обновление текущих цен через API Московской биржи."""
        assets = get_all_assets(self.current_broker_id)
        
        if not assets:
            messagebox.showinfo("Информация", "Нет активов для обновления цен")
            return
        
        total = len(assets)
        success = 0
        failed = 0
        not_found = 0
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Проверяем интернет
        if not is_connected():
            messagebox.showwarning("Нет интернета", 
                "Подключение к интернету отсутствует. Будут показаны последние сохранённые цены.")
            self.status_var.set("Нет интернета. Используются сохранённые цены.")
            self.refresh()
            return
        
        # Обновляем каждый тикер
        for i, asset in enumerate(assets, 1):
            asset_id = asset["id"]
            ticker = asset["ticker"]
            asset_type = asset["asset_type"]
            
            try:
                price, error = fetch_price(ticker, asset_type)
                
                if price is not None:
                    static_data = None
                    if asset_type == "облигация":
                        static_data = _fetch_bond_static(ticker)
                    if static_data:
                        fv = static_data.get("face_value") or (asset["face_value"] or 1000)
                        ls = static_data.get("lot_size") or 1
                        lv = fv * ls if fv and ls else fv
                        ll = static_data.get("list_level")
                        cp = static_data.get("coupon_percent")
                        update_asset_price(asset_id, price, now,
                                         face_value=fv, lot_size=ls, lot_value=lv, list_level=ll, coupon_percent=cp)
                    else:
                        update_asset_price(asset_id, price, now)
                    success += 1
                    self.status_var.set(f"[{i}/{total}] {ticker}: {price:.2f} ✓")
                else:
                    not_found += 1
                    self.status_var.set(f"[{i}/{total}] {ticker}: не найдено на бирже")
                    
            except Exception as e:
                failed += 1
                self.status_var.set(f"[{i}/{total}] {ticker}: ошибка - {str(e)}")
            
            # Обновляем UI для отображения прогресса
            self.refresh()
            self.update_idletasks()
        
        # Финальное обновление
        self.refresh()
        
        result_msg = f"Успешно: {success}"
        if not_found > 0:
            result_msg += f", не найдено: {not_found}"
        if failed > 0:
            result_msg += f", ошибок: {failed}"
        
        messagebox.showinfo("Результат", result_msg)
        self.status_var.set(result_msg)

    def _is_price_stale(self, last_update_str):
        """
        Проверить, устарела ли цена.
        Цена считается устаревшей, если ей больше 1 дня или её нет вообще.
        """
        if not last_update_str:
            return True
        
        try:
            last_dt = datetime.strptime(last_update_str, "%Y-%m-%d %H:%M:%S")
            now = datetime.now()
            diff = now - last_dt
            # Если прошло больше 1 дня — считаем устаревшей
            return diff.total_seconds() > 86400
        except (ValueError, TypeError):
            return True

    def refresh(self):
        """Обновление таблицы."""
        try:
            children = self.tree.tk.call(self.tree._w, "children", "")
        except Exception:
            return  # виджет уже удалён
        # Очистка
        for item in children:
            self.tree.delete(item)

        # Загрузка данных
        assets = get_all_assets(self.current_broker_id)
        
        if not assets:
            self.status_var.set("Нет активов")
            return

        # Получаем курсы валют
        rates = get_exchange_rates()

        # Группировка по брокерам
        grouped = {}
        for asset in assets:
            bid = asset["broker_id"]
            if bid not in grouped:
                grouped[bid] = []
            grouped[bid].append(asset)

        # Отображение с группировкой (сортировка: None/0 в начало, потом по ID)
        def sort_key(x):
            if x is None or x == 0:
                return ""
            return str(x)
        
        sorted_broker_ids = sorted(grouped.keys(), key=sort_key)
        
        for broker_id in sorted_broker_ids:
            broker_assets = grouped[broker_id]
            broker_name = self._get_account_name(broker_id)
            
            # Заголовок группы (счёт)
            group_label = f"── {broker_name} ──" if broker_name != "—" else "── Не указан ──"
            self.tree.insert('', tk.END, values=group_label, tag='group_header')

            for asset in broker_assets:
                currency = self._get_currency_from_asset(asset)
                
                # Рассчитываем общую стоимость в рублях (по текущей цене)
                price_for_total = asset["current_price"] or asset["avg_price"]
                if asset["asset_type"] == "облигация":
                    fv = asset["face_value"] or 1000
                    total_rub = asset["quantity"] * price_for_total * fv / 100
                else:
                    total_rub = asset["quantity"] * price_for_total
                if currency == "USD":
                    total_rub *= rates.get("USD", 90.0)
                elif currency == "EUR":
                    total_rub *= rates.get("EUR", 100.0)
                elif currency == "CNY":
                    total_rub *= rates.get("CNY", 12.0)
                
                type_map = {"акция": "Акция", "облигация": "Облигация", "etf": "ETF"}
                display_type = type_map.get(asset["asset_type"], asset["asset_type"])
                
                # Название бумаги
                name = asset["name"] or "—"
                
                # Текущая цена с пометкой о актуальности
                current_price = asset["current_price"] or 0
                last_update = asset["last_update"]
                
                # Проверяем актуальность цены
                is_stale = self._is_price_stale(last_update)
                
                if current_price > 0 and not is_stale:
                    price_str = f"{current_price:.2f}"
                    update_display = last_update[:10] if last_update else "never"
                elif current_price > 0 and is_stale:
                    price_str = f"{current_price:.2f} (не актуально)"
                    update_display = (last_update[:10] if last_update else "never") + " [старая]"
                else:
                    price_str = "нет данных"
                    update_display = "never"
                
                # Уровень листинга
                list_level = asset["list_level"]
                list_level_display = str(list_level) if list_level is not None else "—"
                
                # Стоимость лота
                lot_value = asset["lot_value"] or 1000
                lot_value_display = f"{lot_value:.2f}" if lot_value > 0 else "—"
                # Проверяем, что это облигация — для акций/ETF показываем прочёрк
                if asset["asset_type"] != "облигация":
                    list_level_display = "—"
                    lot_value_display = "—"
                
                # Купонная ставка
                cp = asset["coupon_percent"]
                coupon_display = f"{cp:.2f}" if cp is not None else "—"

                self.tree.insert('', tk.END, values=(
                    name,
                    asset["ticker"],
                    coupon_display,
                    display_type,
                    list_level_display,
                    asset["quantity"],
                    lot_value_display,
                    f"{asset['avg_price']:.2f}",
                    price_str,
                    f"{total_rub:.2f}",
                    asset["purchase_date"],
                    update_display,
                    currency
                ), tags=(str(asset["id"]),))

        self.status_var.set(f"Всего активов: {len(assets)}")
        apply_zebra(self.tree)

    def _edit_asset(self):
        """Открытие формы редактирования актива."""
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("Внимание", "Выберите актив для редактирования")
            return

        item = self.tree.item(selected[0])
        values = item['values']
        asset_id = item['tags'][0] if item['tags'] else None
        if not asset_id:
            messagebox.showerror("Ошибка", "Не удалось определить актив")
            return
        asset_id = int(asset_id)

        asset = get_asset(asset_id)
        if not asset:
            messagebox.showerror("Ошибка", "Актив не найден")
            return

        # Получаем список счетов
        accounts = get_all_accounts()
        account_options = {}
        for a in accounts:
            acct_num = a["account_number"] if "account_number" in a.keys() else ""
            if acct_num:
                display = f'{a["name"]} · {acct_num}'
            else:
                display = a["name"]
            account_options[display] = a["id"]

        dialog = tb.Toplevel(self.master)
        dialog.title(f"Редактировать {asset['ticker']}")
        dialog.geometry("400x420")
        dialog.transient(self.master)
        dialog.grab_set()
        self._center_on_parent(dialog)


        # Поля формы с предзаполленными данными
        row = 0
        entries = []

        # Название
        tb.Label(dialog, text="Название").grid(row=row, column=0, sticky=tk.W, padx=10, pady=5)
        name_val = asset["name"] if "name" in asset.keys() else ""
        name_var = tk.StringVar(value=name_val or "")
        e = tb.Entry(dialog, textvariable=name_var)
        e.grid(row=row, column=1, padx=10, pady=5)
        entries.append(e)
        row += 1

        # Код ценной бумаги
        tb.Label(dialog, text="Код ценной бумаги").grid(row=row, column=0, sticky=tk.W, padx=10, pady=5)
        var = tk.StringVar(value=asset["ticker"])
        e = tb.Entry(dialog, textvariable=var)
        e.grid(row=row, column=1, padx=10, pady=5)
        entries.append(e)
        row += 1

        # Тип
        tb.Label(dialog, text="Тип").grid(row=row, column=0, sticky=tk.W, padx=10, pady=5)
        type_var = tk.StringVar(value=asset["asset_type"])
        combo = tb.Combobox(dialog, textvariable=type_var, values=["акция", "облигация", "etf"], state="readonly")
        combo.grid(row=row, column=1, padx=10, pady=5)
        row += 1

        # Валюта
        tb.Label(dialog, text="Валюта").grid(row=row, column=0, sticky=tk.W, padx=10, pady=5)
        currency = self._get_currency_from_asset(asset)
        currencies = get_currencies()
        currency_codes = [c["code"] for c in currencies]
        currency_var = tk.StringVar(value=currency)
        currency_combo = tb.Combobox(dialog, textvariable=currency_var, values=currency_codes, state="readonly", width=28)
        currency_combo.grid(row=row, column=1, padx=10, pady=5)
        row += 1

        # Счёт
        tb.Label(dialog, text="Счёт").grid(row=row, column=0, sticky=tk.W, padx=10, pady=5)
        current_account_name = "Не указан"
        account_id_val = asset["broker_id"] if "broker_id" in asset.keys() else None
        if account_id_val:
            a = get_account(account_id_val)
            if a:
                acct_num = a["account_number"] if "account_number" in a.keys() else ""
                if acct_num:
                    current_account_name = f'{a["name"]} · {acct_num}'
                else:
                    current_account_name = a["name"]
        account_var = tk.StringVar(value=current_account_name)
        account_values = ["Не указан"] + list(account_options.keys())
        account_combo = tb.Combobox(dialog, textvariable=account_var, values=account_values, state="readonly")
        account_combo.grid(row=row, column=1, padx=10, pady=5)
        row += 1

        tb.Label(dialog, text="Количество").grid(row=row, column=0, sticky=tk.W, padx=10, pady=5)
        qty_var = tk.StringVar(value=str(asset["quantity"]))
        e = tb.Entry(dialog, textvariable=qty_var)
        e.grid(row=row, column=1, padx=10, pady=5)
        entries.append(e)
        row += 1

        tb.Label(dialog, text="Цена покупки").grid(row=row, column=0, sticky=tk.W, padx=10, pady=5)
        price_var = tk.StringVar(value=str(asset["avg_price"]))
        e = tb.Entry(dialog, textvariable=price_var)
        e.grid(row=row, column=1, padx=10, pady=5)
        entries.append(e)
        row += 1

        tb.Label(dialog, text="Дата покупки").grid(row=row, column=0, sticky=tk.W, padx=10, pady=5)
        purchase_date_display = asset["purchase_date"]
        initial_date = None
        if purchase_date_display:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                try:
                    initial_date = datetime.strptime(purchase_date_display, fmt).date()
                    break
                except (ValueError, TypeError):
                    continue
        if not initial_date and len(purchase_date_display) >= 10:
            try:
                initial_date = datetime.strptime(purchase_date_display[:10], "%Y-%m-%d").date()
            except (ValueError, TypeError):
                pass
        if not initial_date:
            initial_date = datetime.now().date()
        date_entry = create_date_entry(dialog, initial_date=initial_date, width=28)
        date_entry.grid(row=row, column=1, padx=10, pady=5)
        entries.append(date_entry)
        row += 1

        for entry in entries:
            if isinstance(entry, tb.Entry):
                _bind_entry_context_menu(entry)

        def on_save():
            try:
                ticker_new = var.get().strip()
                name = name_var.get().strip()
                asset_type = type_var.get()
                currency = currency_var.get()
                account_name = account_var.get()
                quantity = float(qty_var.get())
                price = float(price_var.get())
                purchase_date = date_entry.get_date()
                purchase_date = purchase_date.strftime("%Y-%m-%d")

                if not ticker_new or not name or quantity <= 0 or price <= 0:
                    messagebox.showerror("Ошибка", "Заполните все поля корректно (название обязательно)")
                    return

                # Определяем account_id
                account_id = None
                if account_name != "Не указан":
                    account_id = account_options.get(account_name)

                update_asset(asset_id, ticker_new, asset_type, quantity, price, purchase_date, broker_id=account_id, name=name, currency=currency)
                messagebox.showinfo("Успех", f"Актив {ticker_new} обновлён")
                dialog.destroy()
                self.refresh()
            except ValueError:
                messagebox.showerror("Ошибка", "Некорректный ввод чисел")

        btn_frame = tb.Frame(dialog)
        btn_frame.grid(row=row, column=0, columnspan=2, pady=10)

        tb.Button(btn_frame, text="Сохранить", command=on_save, bootstyle="success").pack(side=tk.LEFT, padx=5)
        tb.Button(btn_frame, text="Отмена", command=dialog.destroy, bootstyle="secondary").pack(side=tk.LEFT, padx=5)

    def _remove_asset(self):
        """Удаление актива."""
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("Внимание", "Выберите актив для удаления")
            return

        item = self.tree.item(selected[0])
        asset_id = item['tags'][0] if item['tags'] else None
        if not asset_id:
            messagebox.showerror("Ошибка", "Не удалось определить актив для удаления")
            return
        asset_id = int(asset_id)
        asset = get_asset(asset_id)
        ticker = asset["ticker"] if asset else "Неизвестно"

        if messagebox.askyesno("Подтверждение", f"Удалить актив {ticker}?"):
            remove_asset(asset_id)
            messagebox.showinfo("Успех", f"Актив {ticker} удалён")
            self.refresh()

    def _save_snapshot(self):
        """Сохранить срез портфеля."""
        from snapshot_guard import save_snapshot_full
        result = save_snapshot_full(self, self.status_var)
        if result is None:
            return  # отменено

    def _sell_asset(self):
        """Открытие формы продажи актива."""
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("Внимание", "Выберите актив для продажи")
            return

        item = self.tree.item(selected[0])
        values = item['values']
        asset_id = item['tags'][0] if item['tags'] else None
        if not asset_id:
            messagebox.showerror("Ошибка", "Не удалось определить актив для продажи")
            return
        asset_id = int(asset_id)
        asset = get_asset(asset_id)
        if not asset:
            messagebox.showerror("Ошибка", "Актив не найден")
            return
        ticker = asset["ticker"]
        currency = asset["currency_code"] or "RUB"
        asset_type = asset["asset_type"]
        quantity = asset["quantity"]
        avg_price = asset["avg_price"]
        account_id = asset["broker_id"]
        account_name = self._get_account_name(account_id) if account_id else "—"

        dialog = tb.Toplevel(self.master)
        dialog.title(f"Продать {ticker}")
        dialog.geometry("420x400")
        dialog.transient(self.master)
        dialog.grab_set()
        self._center_on_parent(dialog)


        # Информация
        info_frame = tb.Frame(dialog, padding=10)
        info_frame.pack(fill=tk.X)

        tb.Label(info_frame, text=f"Код: {ticker}").pack(anchor=tk.W)
        tb.Label(info_frame, text=f"Название: {values[0]}").pack(anchor=tk.W)
        tb.Label(info_frame, text=f"Количество: {quantity}").pack(anchor=tk.W)
        tb.Label(info_frame, text=f"Счёт: {account_name}").pack(anchor=tk.W)
        tb.Label(info_frame, text=f"Валюта: {currency}").pack(anchor=tk.W)
        tb.Label(info_frame, text=f"Средняя цена: {avg_price:.2f}").pack(anchor=tk.W)

        # Получаем курсы для расчёта стоимости в рублях
        rates = get_exchange_rates()
        current_price = asset["current_price"] or avg_price
        if asset_type == "облигация":
            fv = asset["face_value"] or 1000
            total_rub = quantity * current_price * fv / 100
        else:
            total_rub = quantity * current_price
        if currency == "USD":
            total_rub *= rates.get("USD", 90.0)
        elif currency == "EUR":
            total_rub *= rates.get("EUR", 100.0)
        elif currency == "CNY":
            total_rub *= rates.get("CNY", 12.0)
        tb.Label(info_frame, text=f"Стоимость: {total_rub:.2f} ₽").pack(anchor=tk.W)

        # Цена продажи
        tb.Label(dialog, text="Цена продажи:").pack(pady=(10, 0))
        sell_price_var = tk.StringVar()
        sell_price_entry = tb.Entry(dialog, textvariable=sell_price_var)
        sell_price_entry.pack(pady=5)
        _bind_entry_context_menu(sell_price_entry)

        # Дата продажи
        tb.Label(dialog, text="Дата продажи:").pack()
        sell_date_entry = create_date_entry(dialog, initial_date=datetime.now().date())
        sell_date_entry.pack(pady=5)

        # Количество и кнопка "Все"
        tb.Label(dialog, text="Количество:").pack(pady=(5, 0))
        sell_qty_frame = tb.Frame(dialog)
        sell_qty_frame.pack(pady=5, fill=tk.X, padx=10)

        sell_qty_var = tk.StringVar()
        sell_qty_entry = tb.Entry(sell_qty_frame, textvariable=sell_qty_var)
        sell_qty_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        _bind_entry_context_menu(sell_qty_entry)

        sell_all_btn = tb.Button(sell_qty_frame, text="Все", width=8)
        sell_all_btn.pack(side=tk.RIGHT)

        def on_sell_all():
            sell_qty_var.set(str(quantity))

        sell_all_btn.config(command=on_sell_all)

        def on_sell():
            try:
                sell_price = float(sell_price_var.get())
                sell_date = sell_date_entry.get_date().strftime("%Y-%m-%d")

                qty_text = sell_qty_var.get().strip()
                if not qty_text:
                    messagebox.showerror("Ошибка", "Введите количество для продажи")
                    return
                sold_qty = float(qty_text)
                if sold_qty <= 0:
                    messagebox.showerror("Ошибка", "Количество должно быть больше 0")
                    return
                if sold_qty > quantity + 0.0001:
                    messagebox.showerror("Ошибка", f"Недостаточно: доступно {quantity}")
                    return

                result, success, msg = sell_asset(asset_id, sell_price, sell_date, quantity=sold_qty)
                if success:
                    messagebox.showinfo("Успех", f"{msg}")
                    dialog.destroy()
                    self.refresh()
            except ValueError:
                messagebox.showerror("Ошибка", "Некорректный ввод")

        # Кнопки
        btn_frame = tb.Frame(dialog)
        btn_frame.pack(pady=10)

        tb.Button(btn_frame, text="Продать", command=on_sell, bootstyle="warning").pack(side=tk.LEFT, padx=5)
        tb.Button(btn_frame, text="Отмена", command=dialog.destroy, bootstyle="secondary").pack(side=tk.LEFT, padx=5)

    def _credit(self):
        """Зачисление купона или дивиденда по активу."""
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("Внимание", "Выберите актив")
            return

        item = self.tree.item(selected[0])
        asset_id = item['tags'][0] if item['tags'] else None
        if not asset_id:
            messagebox.showerror("Ошибка", "Не удалось определить актив")
            return
        asset_id = int(asset_id)
        asset = get_asset(asset_id)
        if not asset:
            messagebox.showerror("Ошибка", "Актив не найден")
            return

        account_id = asset["broker_id"]
        account_name = self._get_account_name(account_id) if account_id else None
        if not account_id:
            messagebox.showwarning("Внимание", "Сначала привяжите актив к счёту через форму редактирования")
            return

        account = get_account(account_id)
        ticker = asset["ticker"]
        name = asset["name"] if "name" in asset.keys() else ticker
        asset_type = asset["asset_type"]

        dialog = tb.Toplevel(self.master)
        dialog.title(f"Купон/Дивиденд: {ticker}")
        dialog.geometry("380x430")
        dialog.transient(self.master)
        dialog.grab_set()
        self._center_on_parent(dialog)


        # Информация об активе
        info_frame = tb.Frame(dialog, padding=10)
        info_frame.pack(fill=tk.X)

        tb.Label(info_frame, text=f"Актив: {ticker}").pack(anchor=tk.W)
        tb.Label(info_frame, text=f"Название: {name}").pack(anchor=tk.W)
        tb.Label(info_frame, text=f"Счёт: {account_name}").pack(anchor=tk.W)
        curr = asset["currency_code"] or (account["currency_code"] if "currency_code" in account.keys() else "RUB")
        tb.Label(info_frame, text=f"Валюта: {curr}").pack(anchor=tk.W)

        # Тип
        tb.Label(dialog, text="Тип").pack(pady=(10, 0))
        kind_var = tk.StringVar(value="купон" if asset_type == "облигация" else "дивиденд")
        tb.Combobox(dialog, textvariable=kind_var,
                      values=["купон", "дивиденд"],
                      state="readonly", width=28).pack(pady=5)

        # Сумма
        tb.Label(dialog, text="Сумма").pack()
        amount_var = tk.StringVar()
        amount_entry = tb.Entry(dialog, textvariable=amount_var)
        amount_entry.pack(pady=5)
        _bind_entry_context_menu(amount_entry)

        # Дата
        tb.Label(dialog, text="Дата").pack()
        date_entry = create_date_entry(dialog, initial_date=datetime.now().date(), width=28)
        date_entry.pack(pady=5)

        # Примечание
        tb.Label(dialog, text="Примечание").pack()
        notes_var = tk.StringVar()
        notes_entry = tb.Entry(dialog, textvariable=notes_var)
        notes_entry.pack(pady=5)
        _bind_entry_context_menu(notes_entry)

        def on_save():
            try:
                amount = float(amount_var.get())
                if amount <= 0:
                    messagebox.showerror("Ошибка", "Сумма должна быть больше 0")
                    return
            except ValueError:
                messagebox.showerror("Ошибка", "Некорректная сумма")
                return

            tx_date = date_entry.get_date().strftime("%Y-%m-%d")
            notes = notes_var.get().strip()

            success, msg = credit_coupon_or_dividend(
                account_id, amount, kind_var.get(), notes, ticker=ticker, tx_date=tx_date
            )
            if success:
                messagebox.showinfo("Успех", msg)
                dialog.destroy()
                self.refresh()
            else:
                messagebox.showerror("Ошибка", msg)

        btn_frame = tb.Frame(dialog)
        btn_frame.pack(pady=10)
        tb.Button(btn_frame, text="Зачислить", command=on_save, bootstyle="primary").pack(side=tk.LEFT, padx=5)
        tb.Button(btn_frame, text="Отмена", command=dialog.destroy, bootstyle="secondary").pack(side=tk.LEFT, padx=5)

    def _buy_asset(self):
        """Открытие формы покупки нового или докупки существующего актива."""
        selected = self.tree.selection()
        account_var = tk.StringVar()
        asset_var = tk.StringVar()  # "Новый" или "ticker · name"
        asset_id_var = tk.StringVar()  # asset_id при выборе существующего актива

        # Get account data
        accounts = get_all_accounts()
        account_options = {}
        for a in accounts:
            acct_num = a["account_number"] if "account_number" in a.keys() else ""
            display = f'{a["name"]} · {acct_num}' if acct_num else a["name"]
            account_options[display] = a["id"]
        account_values = ["Не указан"] + list(account_options.keys())

        dialog = tb.Toplevel(self.master)
        dialog.title("Купить актив")
        dialog.geometry("420x380")
        dialog.transient(self.master)
        dialog.grab_set()
        self._center_on_parent(dialog)

        # Account selector row
        acct_frame = tb.Frame(dialog, padding=10)
        acct_frame.pack(fill=tk.X)
        tb.Label(acct_frame, text="Счёт:").grid(row=0, column=0, sticky=tk.W, padx=(0, 5))
        account_combo = tb.Combobox(acct_frame, textvariable=account_var,
                                      values=account_values, state="readonly", width=35)
        account_combo.grid(row=0, column=1, sticky=tk.W)

        # Asset picker row (всегда виден)
        asset_picker_row = tb.Frame(dialog, padding=(10, 0))
        asset_picker_row.pack(fill=tk.X)
        tb.Label(asset_picker_row, text="Актив:").pack(anchor=tk.W)
        asset_combo = tb.Combobox(asset_picker_row, textvariable=asset_var,
                                    values=[], state="readonly", width=50)
        asset_combo.pack(fill=tk.X, pady=(2, 0))

        # Info fields frame (grid: label | value, label | value)
        info_frame = tb.Frame(dialog)
        info_frame.pack(fill=tk.X, padx=10, pady=5)

        info_fields = {}
        info_entries = {}
        labels_data = [
            ("Код:", 0, 0), ("Название:", 0, 2),
            ("Тип:", 1, 0), ("Валюта:", 1, 2),
        ]
        field_keys = ["ticker", "name", "asset_type", "currency"]
        for i, (label_text, row, col) in enumerate(labels_data):
            tb.Label(info_frame, text=label_text).grid(row=row, column=col, sticky=tk.W, padx=(0, 5), pady=3)
            v = tk.StringVar()
            e = tb.Entry(info_frame, textvariable=v, width=22)
            e.grid(row=row, column=col + 1, sticky=tk.EW, pady=3)
            info_fields[field_keys[i]] = v
            info_entries[field_keys[i]] = e

        info_frame.columnconfigure(1, weight=1)
        info_frame.columnconfigure(3, weight=1)

        # Type combobox
        type_combo_var = tk.StringVar(value="акция")
        type_combo = tb.Combobox(info_frame, textvariable=type_combo_var,
                                   values=["акция", "облигация", "etf"], width=19, state="readonly")
        type_combo.grid(row=1, column=1, sticky=tk.EW, pady=3)

        # Currency combobox
        currencies = get_currencies()
        currency_codes = [c["code"] for c in currencies]
        currency_combo_var = tk.StringVar(value=currency_codes[0] if currency_codes else "RUB")
        currency_combo = tb.Combobox(info_frame, textvariable=currency_combo_var,
                                       values=currency_codes, width=19, state="readonly")
        currency_combo.grid(row=1, column=3, sticky=tk.EW, pady=3)

        # Balance label
        balance_label = tb.Label(dialog, text="Доступно: —")
        balance_label.pack(pady=(2, 2))

        # Input fields: quantity, price, date
        input_frame = tb.Frame(dialog, padding=(10, 0))
        input_frame.pack(fill=tk.X)

        tb.Label(input_frame, text="Количество:").grid(row=0, column=0, sticky=tk.W, padx=(0, 5), pady=5)
        qty_var = tk.StringVar()
        qty_entry = tb.Entry(input_frame, textvariable=qty_var)
        qty_entry.grid(row=0, column=1, sticky=tk.EW, pady=5)

        tb.Label(input_frame, text="Цена:").grid(row=1, column=0, sticky=tk.W, padx=(0, 5), pady=5)
        price_var = tk.StringVar()
        price_entry = tb.Entry(input_frame, textvariable=price_var)
        price_entry.grid(row=1, column=1, sticky=tk.EW, pady=5)

        tb.Label(input_frame, text="Дата покупки:").grid(row=2, column=0, sticky=tk.W, padx=(0, 5), pady=5)
        date_entry = create_date_entry(input_frame, initial_date=datetime.now().date())
        date_entry.grid(row=2, column=1, sticky=tk.EW, pady=5)

        input_frame.columnconfigure(1, weight=1)

        for w in [qty_entry, price_entry]:
            _bind_entry_context_menu(w)

        # ---- Helpers ----
        def _set_fields_editable(edit: bool):
            """Включить/отключить поля ввода информации об активе."""
            info_entries["ticker"].config(state="normal" if edit else "disabled")
            info_entries["name"].config(state="normal" if edit else "disabled")
            type_combo.config(state="readonly" if edit else "disabled")
            currency_combo.config(state="readonly" if edit else "disabled")

        def _update_balance():
            name = account_var.get()
            if name == "Не указан":
                balance_label.config(text="Доступно: —")
                return
            acct_id = account_options.get(name)
            if acct_id:
                acc = get_account(acct_id)
                if acc:
                    bal = acc["balance"] if "balance" in acc.keys() else 0
                    curr = acc["currency_code"] if "currency_code" in acc.keys() else "RUB"
                    balance_label.config(text=f"Доступно: {bal:,.2f} {curr}")

        def _refresh_asset_list():
            """Обновить список активов для выбранного счёта. Первый элемент — «Новый»."""
            acct_name = account_var.get()
            if acct_name == "Не указан":
                all_assets = get_all_assets()
            else:
                acct_id = account_options.get(acct_name)
                all_assets = get_all_assets(broker_id=acct_id) if acct_id else []
            items = ["Новый"]
            for a in all_assets:
                a = dict(a)
                display = f'{a["name"] or ""} · {a["ticker"]}'
                items.append(display)
            asset_combo.config(values=items)
            asset_var.set("Новый")
            asset_id_var.set("")

        def _on_asset_select(*_):
            sel = asset_var.get()
            if sel == "Новый":
                _set_fields_editable(True)
                for k in info_fields:
                    info_fields[k].set("")
                type_combo_var.set("акция")
                currency_combo_var.set("RUB")
                asset_id_var.set("")
            else:
                _set_fields_editable(False)
                broker = None
                acct_name = account_var.get()
                if acct_name != "Не указан":
                    broker = account_options.get(acct_name)
                for a in get_all_assets(broker_id=broker):
                    a = dict(a)
                    display = f'{a["name"] or ""} · {a["ticker"]}'
                    if display == sel:
                        asset_id_var.set(str(a["id"]))
                        info_fields["ticker"].set(a["ticker"])
                        info_fields["name"].set(a["name"] or "")
                        info_fields["asset_type"].set(a["asset_type"])
                        info_fields["currency"].set(a["currency_code"] or "RUB")
                        type_combo_var.set(a["asset_type"])
                        currency_combo_var.set(a["currency_code"] or "RUB")
                        break

        def _on_ticker_autofill(*_):
            """Автоподстановка названия тикера из реестра."""
            ticker = info_fields["ticker"].get().strip()
            if not ticker:
                return
            # Подставляем только если имя ещё не введено
            if info_fields["name"].get().strip():
                return
            name = get_ticker_name(ticker)
            if name:
                info_fields["name"].set(name)

        info_fields["ticker"].trace_add("write", _on_ticker_autofill)

        # ---- Initial state ----
        _refresh_asset_list()
        _update_balance()

        # Pre-select asset if a row was selected in the tree
        if selected:
            item = self.tree.item(selected[0])
            sel_asset_id = item['tags'][0] if item['tags'] else None
            if sel_asset_id:
                asset = get_asset(int(sel_asset_id))
                if asset:
                    asset = dict(asset)
                    if asset["broker_id"]:
                        for an in account_values:
                            aid_check = account_options.get(an)
                            if aid_check == asset["broker_id"]:
                                account_var.set(an)
                                break
                    _refresh_asset_list()
                    display = f'{asset["name"] or ""} · {asset["ticker"]}'
                    asset_var.set(display)
                    _on_asset_select()

        # ---- Bindings ----
        account_var.trace_add("write", lambda *_: (_refresh_asset_list(), _update_balance()))
        asset_var.trace_add("write", _on_asset_select)

        # ---- Save handler ----
        def on_buy():
            try:
                qty = float(qty_var.get())
                price = float(price_var.get())
                buy_date = date_entry.get_date().strftime("%Y-%m-%d")

                if qty <= 0 or price <= 0:
                    messagebox.showerror("Ошибка", "Количество и цена должны быть больше 0")
                    return

                if asset_id_var.get():
                    # Существующий актив — докупка
                    aid = int(asset_id_var.get())
                    result, success, msg = buy_more_asset(aid, qty, price, buy_date)
                    if success:
                        messagebox.showinfo("Успех",
                                            f"{result['ticker']}: докуплено.\n"
                                            f"Новое количество: {result['new_qty']:.2f}\n"
                                            f"Новая средняя цена: {result['new_avg_price']:.2f}\n"
                                            f"{msg}")
                        dialog.destroy()
                        self.refresh()
                    else:
                        messagebox.showerror("Ошибка", msg)
                else:
                    # Новый актив
                    ticker = info_fields["ticker"].get().strip()
                    name = info_fields["name"].get().strip()
                    asset_type = type_combo_var.get()
                    currency = currency_combo_var.get()
                    acct_name = account_var.get()
                    account_id = None
                    if acct_name != "Не указан":
                        account_id = account_options.get(acct_name)

                    if not ticker or not name:
                        messagebox.showerror("Ошибка", "Тикер и название обязательно")
                        return

                    # Проверка дубликата тикера
                    all_assets = get_all_assets()
                    for a in all_assets:
                        a = dict(a)
                        if a["ticker"] == ticker:
                            r = messagebox.askyesnocancel(
                                "Дубликат тикера",
                                f"Актив с тикером {ticker} уже существует.\n"
                                "Переключиться на докупку?",
                                icon="warning"
                            )
                            if r is True:  # Yes — переключиться на докупку
                                if a["broker_id"]:
                                    for an in account_values:
                                        aid_check = account_options.get(an)
                                        if aid_check == a["broker_id"]:
                                            account_var.set(an)
                                            break
                                    _refresh_asset_list()
                                asset_id_var.set(str(a["id"]))
                                _set_fields_editable(False)
                                info_fields["ticker"].set(a["ticker"])
                                info_fields["name"].set(a["name"] or "")
                                info_fields["asset_type"].set(a["asset_type"])
                                info_fields["currency"].set(a["currency_code"] or "RUB")
                                type_combo_var.set(a["asset_type"])
                                currency_combo_var.set(a["currency_code"] or "RUB")
                            elif r is False:  # No — создать дубликат
                                break
                            else:
                                return  # Cancel
                            break

                    result, success, msg = add_asset(ticker, asset_type, qty, price, buy_date,
                                                      account_id=account_id, name=name, currency_code=currency)
                    if success:
                        messagebox.showinfo("Успех", f"Актив {ticker} добавлен\n{msg}")
                    else:
                        messagebox.showerror("Ошибка", msg)
                        return
                    dialog.destroy()
                    self.refresh()
            except ValueError:
                messagebox.showerror("Ошибка", "Некорректный ввод чисел")

        # ---- Buttons ----
        btn_frame = tb.Frame(dialog)
        btn_frame.pack(pady=10)
        tb.Button(btn_frame, text="Купить", command=on_buy, bootstyle="success").pack(side=tk.LEFT, padx=5)
        tb.Button(btn_frame, text="Отмена", command=dialog.destroy, bootstyle="secondary").pack(side=tk.LEFT, padx=5)

import tkinter as tk
import ttkbootstrap as tb
from tkinter import messagebox
from database import (
    get_all_accounts, get_account, update_account, delete_account,
    toggle_account_active, add_account, get_account_balance, change_account_balance,
    deposit, withdraw, get_all_transactions,
    get_connection, display_type, get_currencies, get_currency_id
)

_TYPE_DISPLAY_TO_INTERNAL = {'Мос.Биржа': 'stock', 'Крипто биржа': 'crypto'}
from datetime import datetime
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


def _format_currency(value, currency):
    """Форматировать значение с валютой."""
    symbols = {'RUB': '₽', 'USD': '$', 'EUR': '€', 'CNY': '¥'}
    return f"{value:,.2f} {symbols.get(currency, currency)}"


class AccountsView(tb.Frame):
    """Окно управления депозитными счетами."""

    def __init__(self, parent, controller=None):
        super().__init__(parent)
        self.controller = controller
        self._create_ui()
        self.refresh()

    def _create_ui(self):
        """Создание интерфейса."""
        # Рамка таблицы
        table_frame = tb.Frame(self)
        table_frame.pack(fill=tk.BOTH, expand=True, pady=5, padx=5)

        # Treeview
        columns = ('id', 'name', 'account_number', 'balance', 'currency', 'type', 'active')
        self.tree = tb.Treeview(table_frame, columns=columns, show='headings', height=20)

        self.tree.heading('id', text='ID')
        self.tree.heading('name', text='Название')
        self.tree.heading('account_number', text='Счёт')
        self.tree.heading('balance', text='На счету')
        self.tree.heading('currency', text='Валюта')
        self.tree.heading('type', text='Тип')
        self.tree.heading('active', text='Активен')

        self.tree.column('id', width=40, anchor=tk.CENTER)
        self.tree.column('name', width=100)
        self.tree.column('account_number', width=100)
        self.tree.column('balance', width=120, anchor=tk.E)
        self.tree.column('currency', width=60, anchor=tk.CENTER)
        self.tree.column('type', width=90, anchor=tk.CENTER)
        self.tree.column('active', width=60, anchor=tk.CENTER)

        # Скроллбар
        scrollbar = tb.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscroll=scrollbar.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Кнопки управления
        btn_frame = tb.Frame(self)
        btn_frame.pack(fill=tk.X, pady=5, padx=5)

        tb.Button(btn_frame, text="Добавить", command=self._add_account, bootstyle="success").pack(side=tk.LEFT, padx=2)
        tb.Button(btn_frame, text="Редактировать", command=self._edit_account, bootstyle="info").pack(side=tk.LEFT, padx=2)
        tb.Button(btn_frame, text="Удалить", command=self._delete_account, bootstyle="danger").pack(side=tk.LEFT, padx=2)
        tb.Button(btn_frame, text="Активность", command=self._toggle_active, bootstyle="warning").pack(side=tk.LEFT, padx=2)

        # Кнопки операций с балансом
        tb.Button(btn_frame, text="Пополнение", command=self._deposit, bootstyle="success").pack(side=tk.LEFT, padx=2)
        tb.Button(btn_frame, text="Списание", command=self._withdraw, bootstyle="danger").pack(side=tk.LEFT, padx=2)
        tb.Button(btn_frame, text="Обновить", command=self.refresh, bootstyle="info").pack(side=tk.LEFT, padx=2)

        # Статус бар
        self.status_var = tk.StringVar(value="Готово")
        status_bar = tb.Label(self, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W)
        status_bar.pack(fill=tk.X, side=tk.BOTTOM, padx=5, pady=(0, 5))

        # Двойной клик — редактировать
        self.tree.bind('<Double-1>', lambda e: self._edit_account())

    def refresh(self):
        """Обновление таблицы."""
        try:
            children = self.tree.tk.call(self.tree._w, "children", "")
        except Exception:
            return
        for item in children:
            self.tree.delete(item)

        accounts = get_all_accounts()

        if not accounts:
            self.status_var.set("Нет счетов")
            return

        for acc in accounts:
            # Определяем тип
            type_map = {'stock': 'Мос.Биржа', 'crypto': 'Крипто биржа'}
            type_display = type_map.get(acc["broker_type"], acc["broker_type"])

            # Определяем валюту
            currency = acc["currency_code"] if "currency_code" in acc.keys() else "RUB"

            # Счёт
            account_number = acc["account_number"] if "account_number" in acc.keys() else ""

            # Форматируем баланс
            balance_val = acc["balance"] if "balance" in acc.keys() else 0
            balance_str = _format_currency(balance_val, currency)

            active_display = "Да" if "active" in acc.keys() and acc["active"] == 1 else "Нет"

            self.tree.insert('', tk.END, values=(
                acc["id"],
                acc["name"],
                account_number,
                balance_str,
                currency,
                type_display,
                active_display
            ), tags=(str(acc["id"]),))

        apply_zebra(self.tree)
        self.status_var.set(f"Всего счетов: {len(accounts)}")

    def _get_selected_id(self):
        """Получить ID выбранного счёта."""
        selected = self.tree.selection()
        if not selected:
            return None
        item = self.tree.item(selected[0])
        return int(item['values'][0])

    def _add_account(self):
        """Открытие формы добавления счёта."""
        dialog = tb.Toplevel(self.master)
        dialog.title("Добавить счёт")
        dialog.geometry("400x280")
        dialog.transient(self.master)
        dialog.grab_set()

        row = 0

        tb.Label(dialog, text="Название").grid(row=row, column=0, sticky=tk.W, padx=10, pady=5)
        name_var = tk.StringVar()
        name_entry = tb.Entry(dialog, textvariable=name_var)
        name_entry.grid(row=row, column=1, padx=10, pady=5)
        _bind_entry_context_menu(name_entry)
        row += 1

        tb.Label(dialog, text="Счёт").grid(row=row, column=0, sticky=tk.W, padx=10, pady=5)
        account_number_var = tk.StringVar()
        account_number_entry = tb.Entry(dialog, textvariable=account_number_var)
        account_number_entry.grid(row=row, column=1, padx=10, pady=5)
        _bind_entry_context_menu(account_number_entry)
        row += 1

        tb.Label(dialog, text="Тип").grid(row=row, column=0, sticky=tk.W, padx=10, pady=5)
        type_var = tk.StringVar(value="Мос.Биржа")
        tb.Combobox(dialog, textvariable=type_var,
                      values=["Мос.Биржа", "Крипто биржа"],
                      state="readonly", width=28).grid(row=row, column=1, padx=10, pady=5)
        row += 1

        tb.Label(dialog, text="Валюта").grid(row=row, column=0, sticky=tk.W, padx=10, pady=5)
        currencies = get_currencies()
        currency_codes = [c["code"] for c in currencies]
        currency_var = tk.StringVar(value=currency_codes[0] if currency_codes else "RUB")
        tb.Combobox(dialog, textvariable=currency_var, values=currency_codes,
                      state="readonly", width=28).grid(row=row, column=1, padx=10, pady=5)
        row += 1

        tb.Label(dialog, text="Начальный баланс").grid(row=row, column=0, sticky=tk.W, padx=10, pady=5)
        balance_var = tk.StringVar(value="0")
        balance_entry = tb.Entry(dialog, textvariable=balance_var)
        balance_entry.grid(row=row, column=1, padx=10, pady=5)
        _bind_entry_context_menu(balance_entry)
        row += 1

        def on_save():
            name = name_var.get().strip()
            if not name:
                messagebox.showerror("Ошибка", "Введите название счёта")
                return
            try:
                balance = float(balance_var.get())
            except ValueError:
                messagebox.showerror("Ошибка", "Некорректный баланс")
                return

            currency_id_val = get_currency_id(currency_var.get())
            account_id = add_account(name, account_number_var.get().strip(), _TYPE_DISPLAY_TO_INTERNAL.get(type_var.get(), type_var.get()), currency_var.get(), balance)
            if account_id:
                messagebox.showinfo("Успех", f"Счёт '{name}' добавлен")
                dialog.destroy()
                self.refresh()
                if self.controller:
                    self.controller._update_account_combo()
            else:
                messagebox.showerror("Ошибка", "Счёт с таким названием уже существует")

        btn_frame = tb.Frame(dialog)
        btn_frame.grid(row=row, column=0, columnspan=2, pady=10)
        tb.Button(btn_frame, text="Сохранить", command=on_save, bootstyle="success").pack(side=tk.LEFT, padx=5)
        tb.Button(btn_frame, text="Отмена", command=dialog.destroy, bootstyle="secondary").pack(side=tk.LEFT, padx=5)

    def _edit_account(self):
        """Открытие формы редактирования счёта."""
        account_id = self._get_selected_id()
        if not account_id:
            messagebox.showwarning("Внимание", "Выберите счёт для редактирования")
            return

        account = get_account(account_id)
        if not account:
            messagebox.showerror("Ошибка", "Счёт не найден")
            return

        dialog = tb.Toplevel(self.master)
        dialog.title(f"Редактировать: {account['name']}")
        dialog.geometry("400x320")
        dialog.transient(self.master)
        dialog.grab_set()

        row = 0

        tb.Label(dialog, text="Название").grid(row=row, column=0, sticky=tk.W, padx=10, pady=5)
        name_var = tk.StringVar(value=account["name"])
        name_entry = tb.Entry(dialog, textvariable=name_var)
        name_entry.grid(row=row, column=1, padx=10, pady=5)
        _bind_entry_context_menu(name_entry)
        row += 1

        acct_num = account["account_number"] if "account_number" in account.keys() else ""
        tb.Label(dialog, text="Счёт").grid(row=row, column=0, sticky=tk.W, padx=10, pady=5)
        account_number_var = tk.StringVar(value=acct_num)
        account_number_entry = tb.Entry(dialog, textvariable=account_number_var)
        account_number_entry.grid(row=row, column=1, padx=10, pady=5)
        _bind_entry_context_menu(account_number_entry)
        row += 1

        tb.Label(dialog, text="Тип").grid(row=row, column=0, sticky=tk.W, padx=10, pady=5)
        type_var = tk.StringVar(value=display_type(account["broker_type"]))
        tb.Combobox(dialog, textvariable=type_var,
                      values=["Мос.Биржа", "Крипто биржа"],
                      state="readonly", width=28).grid(row=row, column=1, padx=10, pady=5)
        row += 1

        tb.Label(dialog, text="Валюта").grid(row=row, column=0, sticky=tk.W, padx=10, pady=5)
        currency_code = account["currency_code"] if "currency_code" in account.keys() else "RUB"
        currencies = get_currencies()
        currency_codes = [c["code"] for c in currencies]
        currency_var = tk.StringVar(value=currency_code)
        tb.Combobox(dialog, textvariable=currency_var, values=currency_codes,
                      state="readonly", width=28).grid(row=row, column=1, padx=10, pady=5)
        row += 1

        tb.Label(dialog, text="Баланс").grid(row=row, column=0, sticky=tk.W, padx=10, pady=5)
        balance_val = account["balance"] if "balance" in account.keys() else 0
        balance_var = tk.StringVar(value=f"{balance_val:.2f}")
        balance_entry = tb.Entry(dialog, textvariable=balance_var)
        balance_entry.grid(row=row, column=1, padx=10, pady=5)
        _bind_entry_context_menu(balance_entry)
        row += 1

        tb.Label(dialog, text="Активен").grid(row=row, column=0, sticky=tk.W, padx=10, pady=5)
        active_val = 1 if "active" in account.keys() and account["active"] == 1 else 0
        active_var = tk.StringVar(value="Да" if active_val == 1 else "Нет")
        tb.Combobox(dialog, textvariable=active_var,
                      values=["Да", "Нет"],
                      state="readonly", width=28).grid(row=row, column=1, padx=10, pady=5)
        row += 1

        def on_save():
            name = name_var.get().strip()
            if not name:
                messagebox.showerror("Ошибка", "Введите название счёта")
                return
            try:
                balance = float(balance_var.get())
            except ValueError:
                messagebox.showerror("Ошибка", "Некорректный баланс")
                return
            active = 1 if active_var.get() == "Да" else 0

            update_account(account_id, name, account_number_var.get().strip(), _TYPE_DISPLAY_TO_INTERNAL.get(type_var.get(), type_var.get()), currency_var.get(), balance, active)
            messagebox.showinfo("Успех", f"Счёт '{name}' обновлён")
            dialog.destroy()
            self.refresh()
            if self.controller:
                self.controller._update_account_combo()

        btn_frame = tb.Frame(dialog)
        btn_frame.grid(row=row, column=0, columnspan=2, pady=10)
        tb.Button(btn_frame, text="Сохранить", command=on_save, bootstyle="success").pack(side=tk.LEFT, padx=5)
        tb.Button(btn_frame, text="Отмена", command=dialog.destroy, bootstyle="secondary").pack(side=tk.LEFT, padx=5)

    def _delete_account(self):
        """Удаление счёта."""
        account_id = self._get_selected_id()
        if not account_id:
            messagebox.showwarning("Внимание", "Выберите счёт для удаления")
            return

        item = self.tree.item(self.tree.selection()[0])
        account_name = item['values'][1]

        # Проверяем есть ли активы
        from database import get_all_assets
        assets = get_all_assets(account_id)
        if assets:
            messagebox.showwarning("Невозможно удалить",
                                    f"Нельзя удалить счёт '{account_name}', "
                                    f"так как на нём {len(assets)} активов.\n"
                                    f"Сначала перенесите или удалите активы.")
            return

        if messagebox.askyesno("Подтверждение", f"Удалить счёт '{account_name}'?"):
            result = delete_account(account_id)
            if result:
                messagebox.showinfo("Успех", f"Счёт '{account_name}' удалён")
                self.refresh()
                if self.controller:
                    self.controller._update_account_combo()
            else:
                messagebox.showerror("Ошибка", "Не удалось удалить счёт")

    def _toggle_active(self):
        """Переключить статус active счёта."""
        account_id = self._get_selected_id()
        if not account_id:
            messagebox.showwarning("Внимание", "Выберите счёт")
            return

        item = self.tree.item(self.tree.selection()[0])
        account_name = item['values'][1]

        new_active = toggle_account_active(account_id)
        if new_active is not None:
            status = "активирован" if new_active == 1 else "деактивирован"
            messagebox.showinfo("Успех", f"Счёт '{account_name}' {status}")
            self.refresh()

    def _deposit(self):
        """Пополнение счёта."""
        account_id = self._get_selected_id()
        if not account_id:
            messagebox.showwarning("Внимание", "Выберите счёт для пополнения")
            return

        account = get_account(account_id)
        dialog = tb.Toplevel(self.master)
        dialog.title(f"Пополнение: {account['name']}")
        dialog.geometry("350x280")
        dialog.transient(self.master)
        dialog.grab_set()

        tb.Label(dialog, text=f"Счёт: {account['name']}").pack(pady=5)

        tb.Label(dialog, text="Сумма").pack(pady=5)
        amount_var = tk.StringVar()
        amount_entry = tb.Entry(dialog, textvariable=amount_var)
        amount_entry.pack(pady=5)
        _bind_entry_context_menu(amount_entry)

        tb.Label(dialog, text="Дата").pack(pady=5)
        date_entry = create_date_entry(dialog, initial_date=datetime.now().date(), width=28)
        date_entry.pack(pady=5)

        tb.Label(dialog, text="Примечание").pack(pady=5)
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
            success, msg = deposit(account_id, amount, notes_var.get(), tx_date=tx_date)
            if success:
                messagebox.showinfo("Успех", msg)
                dialog.destroy()
                self.refresh()
            else:
                messagebox.showerror("Ошибка", msg)

        btn_frame = tb.Frame(dialog)
        btn_frame.pack(pady=10)
        tb.Button(btn_frame, text="Пополнить", command=on_save, bootstyle="success").pack(side=tk.LEFT, padx=5)
        tb.Button(btn_frame, text="Отмена", command=dialog.destroy, bootstyle="secondary").pack(side=tk.LEFT, padx=5)

    def _withdraw(self):
        """Списание со счёта."""
        account_id = self._get_selected_id()
        if not account_id:
            messagebox.showwarning("Внимание", "Выберите счёт для списания")
            return

        account = get_account(account_id)
        dialog = tb.Toplevel(self.master)
        dialog.title(f"Списание: {account['name']}")
        dialog.geometry("350x300")
        dialog.transient(self.master)
        dialog.grab_set()

        balance_val = account["balance"] if "balance" in account.keys() else 0
        currency = account["currency_code"] if "currency_code" in account.keys() else "RUB"
        tb.Label(dialog, text=f"Счёт: {account['name']}").pack(pady=5)
        tb.Label(dialog, text=f"Доступно: {balance_val:,.2f} {currency}").pack(pady=5)

        tb.Label(dialog, text="Сумма").pack(pady=5)
        amount_var = tk.StringVar()
        amount_entry = tb.Entry(dialog, textvariable=amount_var)
        amount_entry.pack(pady=5)
        _bind_entry_context_menu(amount_entry)

        tb.Label(dialog, text="Дата").pack(pady=5)
        date_entry = create_date_entry(dialog, initial_date=datetime.now().date(), width=28)
        date_entry.pack(pady=5)

        tb.Label(dialog, text="Примечание").pack(pady=5)
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
            success, msg = withdraw(account_id, amount, notes_var.get(), tx_date=tx_date)
            if success:
                messagebox.showinfo("Успех", msg)
                dialog.destroy()
                self.refresh()
            else:
                messagebox.showerror("Ошибка", msg)

        btn_frame = tb.Frame(dialog)
        btn_frame.pack(pady=10)
        tb.Button(btn_frame, text="Списать", command=on_save, bootstyle="danger").pack(side=tk.LEFT, padx=5)
        tb.Button(btn_frame, text="Отмена", command=dialog.destroy, bootstyle="secondary").pack(side=tk.LEFT, padx=5)

import tkinter as tk
import ttkbootstrap as tb
from tkinter import messagebox
from database import get_all_transactions, get_transactions_count, get_transaction_years
from datetime import datetime
import math
from table_utils import apply_zebra


_TYPE_DISPLAY = {
    'пополнение': 'Пополнение',
    'списание': 'Списание',
    'покупка': 'Покупка',
    'продажа': 'Продажа',
    'купон': 'Купон',
    'дивиденд': 'Дивиденд',
}

_TYPE_TO_DB = {
    'Все': None,
    'Пополнение': 'пополнение',
    'Списание': 'списание',
    'Покупка': 'покупка',
    'Продажа': 'продажа',
    'Купон': 'купон',
    'Дивиденд': 'дивиденд',
}

_MONTH_DISPLAY = {
    'Все месяцы': None,
    'Январь': 1, 'Февраль': 2, 'Март': 3, 'Апрель': 4,
    'Май': 5, 'Июнь': 6, 'Июль': 7, 'Август': 8,
    'Сентябрь': 9, 'Октябрь': 10, 'Ноябрь': 11, 'Декабрь': 12,
}

_TYPE_COLORS = {
    'пополнение': 'green',
    'списание': 'red',
    'покупка': 'blue',
    'продажа': 'darkgreen',
    'купон': 'purple',
    'дивиденд': 'purple',
}


class TransactionsView(tb.Frame):
    """Вкладка 'Все транзакции' — история всех операций."""

    def __init__(self, parent, controller=None):
        super().__init__(parent)
        self.controller = controller
        self.current_page = 1
        self.page_size = 100
        self.total_records = 0
        self.total_pages = 1
        self._create_ui()
        self.refresh()

    # ─── UI ──────────────────────────────────────────────────────────

    def _create_ui(self):
        """Создание интерфейса."""
        # Фильтры
        filter_frame = tb.Frame(self)
        filter_frame.pack(fill=tk.X, padx=5, pady=5)

        # Год
        tb.Label(filter_frame, text="Год:").pack(side=tk.LEFT, padx=(0, 2))
        self.year_var = tk.StringVar(value=str(datetime.now().year))
        self.year_combo = tb.Combobox(filter_frame, textvariable=self.year_var,
                                       state="readonly", width=8)
        self.year_combo.pack(side=tk.LEFT, padx=2)
        self._populate_years()
        self.year_combo.bind('<<ComboboxSelected>>', lambda e: self._on_filter_change())

        # Месяц
        tb.Label(filter_frame, text="Месяц:").pack(side=tk.LEFT, padx=(10, 2))
        self.month_var = tk.StringVar(value="Все месяцы")
        self.month_combo = tb.Combobox(filter_frame, textvariable=self.month_var,
                                        state="readonly", width=14)
        self.month_combo.pack(side=tk.LEFT, padx=2)
        self.month_combo['values'] = list(_MONTH_DISPLAY.keys())
        self.month_combo.bind('<<ComboboxSelected>>', lambda e: self._on_filter_change())

        # Тип
        tb.Label(filter_frame, text="Тип:").pack(side=tk.LEFT, padx=(10, 2))
        self.type_var = tk.StringVar(value="Все")
        self.type_combo = tb.Combobox(filter_frame, textvariable=self.type_var,
                                       state="readonly", width=14)
        self.type_combo.pack(side=tk.LEFT, padx=2)
        self.type_combo['values'] = list(_TYPE_TO_DB.keys())
        self.type_combo.bind('<<ComboboxSelected>>', lambda e: self._on_filter_change())

        # Сбросить
        tb.Button(filter_frame, text="Сбросить", command=self._on_reset, bootstyle="secondary").pack(side=tk.LEFT, padx=(10, 0))

        # Таблица
        columns = ('id', 'date', 'type', 'account', 'ticker', 'amount', 'currency', 'notes')
        self.tree = tb.Treeview(self, columns=columns, show='headings', height=20)

        self.tree.heading('id', text='№')
        self.tree.heading('date', text='Дата')
        self.tree.heading('type', text='Тип')
        self.tree.heading('account', text='Счёт')
        self.tree.heading('ticker', text='Название бумаги')
        self.tree.heading('amount', text='Сумма')
        self.tree.heading('currency', text='Валюта')
        self.tree.heading('notes', text='Примечание')

        self.tree.column('id', width=50, anchor=tk.CENTER)
        self.tree.column('date', width=110, anchor=tk.CENTER)
        self.tree.column('type', width=100, anchor=tk.CENTER)
        self.tree.column('account', width=120)
        self.tree.column('ticker', width=150, anchor=tk.CENTER)
        self.tree.column('amount', width=110, anchor=tk.E)
        self.tree.column('currency', width=60, anchor=tk.CENTER)
        self.tree.column('notes', width=200)

        scrollbar = tb.Scrollbar(self, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscroll=scrollbar.set)

        self.tree.pack(fill=tk.BOTH, expand=True, padx=5, pady=(5, 0))
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 5), pady=(5, 0))

        # Пагинация
        self._create_pagination()

    def _create_pagination(self):
        """Рамка пагинации под таблицей."""
        self.pagination_frame = tb.Frame(self)
        self.pagination_frame.pack(fill=tk.X, padx=5, pady=(0, 5))

        self.prev_btn = tb.Button(self.pagination_frame, text="◀ Назад", command=self._prev_page, bootstyle="secondary")
        self.prev_btn.pack(side=tk.LEFT, padx=2)

        self.page_label = tb.Label(self.pagination_frame, text="Страница 1 из 1")
        self.page_label.pack(side=tk.LEFT, padx=10)

        self.next_btn = tb.Button(self.pagination_frame, text="Вперёд ▶", command=self._next_page, bootstyle="secondary")
        self.next_btn.pack(side=tk.LEFT, padx=2)

        self.total_label = tb.Label(self.pagination_frame, text="Всего записей: 0")
        self.total_label.pack(side=tk.LEFT, padx=10)

    def _populate_years(self):
        """Заполнить список лет."""
        years = get_transaction_years()
        self.year_combo['values'] = years

    # ─── Handlers ────────────────────────────────────────────────────

    def _on_filter_change(self):
        """При изменении любого фильтра — сброс на 1-ю страницу + перезагрузка."""
        self.current_page = 1
        self.refresh()

    def _on_reset(self):
        """Сбросить все фильтры на значения по умолчанию."""
        self.year_var.set(str(datetime.now().year))
        self.month_var.set("Все месяцы")
        self.type_var.set("Все")
        self.current_page = 1
        self.refresh()

    def _prev_page(self):
        """Переход на предыдущую страницу."""
        if self.current_page > 1:
            self.current_page -= 1
            self.refresh()

    def _next_page(self):
        """Переход на следующую страницу."""
        if self.current_page < self.total_pages:
            self.current_page += 1
            self.refresh()

    # ─── Refresh ─────────────────────────────────────────────────────

    def refresh(self):
        """Обновление таблицы с фильтрацией и пагинацией."""
        try:
            children = self.tree.tk.call(self.tree._w, "children", "")
        except Exception:
            return

        for item in children:
            self.tree.delete(item)

        # Считаем фильтры
        account_id = None
        if self.controller and hasattr(self.controller, 'selected_account_id'):
            aid = self.controller.selected_account_id.get()
            if aid > 0:
                account_id = aid

        tx_type = _TYPE_TO_DB.get(self.type_var.get())

        year = self.year_var.get() if self.year_var.get() else None
        month = _MONTH_DISPLAY.get(self.month_var.get())

        # Общее количество
        self.total_records = get_transactions_count(account_id, tx_type, year, month)
        self.total_pages = max(1, math.ceil(self.total_records / self.page_size)) if self.total_records > 0 else 1

        # Клампим страницу
        if self.current_page < 1:
            self.current_page = 1
        if self.current_page > self.total_pages:
            self.current_page = self.total_pages

        # Данные
        offset = (self.current_page - 1) * self.page_size
        transactions = get_all_transactions(account_id=account_id, tx_type=tx_type,
                                            year=year, month=month,
                                            limit=self.page_size, offset=offset)

        if not transactions:
            self.total_label.config(text=f"Всего записей: 0")
            self.page_label.config(text=f"Страница 1 из 1")
            self.prev_btn.config(state=tk.DISABLED)
            self.next_btn.config(state=tk.DISABLED)
            return

        for tx in transactions:
            tx_type_display = _TYPE_DISPLAY.get(tx["tx_type"], tx["tx_type"])
            amount_val = tx["amount"]
            color = _TYPE_COLORS.get(tx["tx_type"], '')
            values = (
                tx["id"],
                tx["date"],
                tx_type_display,
                tx["account_name"] or "—",
                tx["asset_name"] or tx["ticker"] or "—",
                f"{amount_val:,.2f}",
                tx["currency_code"] or "RUB",
                tx["notes"] or ""
            )
            tag = ''
            if color:
                tag = 'colored'
            self.tree.insert('', tk.END, values=values, tags=(tag,))
            if tag:
                self.tree.tag_configure(tag, foreground=color)

        # Пагинация
        self.total_label.config(text=f"Всего записей: {self.total_records}")
        self.page_label.config(text=f"Страница {self.current_page} из {self.total_pages}")

        self.prev_btn.config(state=tk.DISABLED if self.current_page <= 1 else tk.NORMAL)
        self.next_btn.config(state=tk.DISABLED if self.current_page >= self.total_pages else tk.NORMAL)

        apply_zebra(self.tree)

import tkinter as tk
import ttkbootstrap as tb
from tkinter import messagebox
from datetime import datetime
import matplotlib
matplotlib.use('TkAgg')
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.transforms import blended_transform_factory
from matplotlib.ticker import FuncFormatter
from database import (
    get_snapshot_years,
    get_analysis_monthly,
    get_transaction_years,
    get_income_transactions,
    get_income_monthly_totals,
    get_exchange_rates,
    get_deal_transactions,
)
from table_utils import apply_zebra

MONTHS_RU = [
    'Янв', 'Фев', 'Мар', 'Апр', 'Май', 'Июн',
    'Июл', 'Авг', 'Сен', 'Окт', 'Ноя', 'Дек',
]


def _rate_for_currency(currency):
    """Курс валюты к рублю (текущий из настроек)."""
    rates = get_exchange_rates()
    mapping = {'USD': 'USD', 'EUR': 'EUR', 'CNY': 'CNY'}
    r_key = mapping.get(currency)
    if r_key:
        return rates.get(r_key, 1.0)
    return 1.0


def _format_rub(value, pos):
    """Форматирование оси Y: млн/тыс с запятой-разделителем."""
    av = abs(value)
    if av >= 1_000_000:
        return f"{value / 1_000_000:.1f}\nмлн".replace('.', ',')
    elif av >= 1_000:
        return f"{value / 1_000:.0f}\nтыс"
    else:
        return f"{value:.0f}"


# ═══════════════════════════════════════════════════════════
#  Вкладка «Динамика портфеля»
# ═══════════════════════════════════════════════════════════

class DynamicsTab(tb.Frame):
    """Вкладка динамики портфеля (существующий код)."""

    def __init__(self, parent, controller=None):
        super().__init__(parent)
        self.controller = controller
        self._create_ui()
        self._update_filters()
        self.refresh()

    # ── получение ID счёта ──────────────────────────────────────

    def _get_account_filter(self):
        if self.controller and hasattr(self.controller, 'selected_account_id'):
            aid = self.controller.selected_account_id.get()
            if aid > 0:
                return aid
        return 'all'

    # ── создание интерфейса ──────────────────────────────────────

    def _create_ui(self):
        filter_frame = tb.Frame(self)
        filter_frame.pack(fill=tk.X, padx=10, pady=(10, 5))

        tb.Label(filter_frame, text="Период:").pack(side=tk.LEFT, padx=(0, 4))
        self.year_from_combo = tb.Combobox(
            filter_frame, state="readonly", width=8, justify=tk.CENTER,
        )
        self.year_from_combo.pack(side=tk.LEFT, padx=(0, 2))
        tb.Label(filter_frame, text="—").pack(side=tk.LEFT, padx=2)
        self.year_to_combo = tb.Combobox(
            filter_frame, state="readonly", width=8, justify=tk.CENTER,
        )
        self.year_to_combo.pack(side=tk.LEFT, padx=(0, 15))
        self.year_from_combo.bind('<<ComboboxSelected>>', lambda e: self.refresh())
        self.year_to_combo.bind('<<ComboboxSelected>>', lambda e: self.refresh())

        chart_frame = tb.Frame(self)
        chart_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(5, 5))

        self.figure = Figure(figsize=(8, 4))
        self.ax = self.figure.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(self.figure, master=chart_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        table_frame = tb.LabelFrame(self, text="Доходность", padx=5, pady=5)
        table_frame.pack(fill=tk.X, padx=10, pady=(0, 10))

        columns = ('month', 'deposits', 'assets', 'balance', 'total', 'income', 'pct')
        self.tree = tb.Treeview(table_frame, columns=columns, show='headings')

        self.tree.heading('month', text='Месяц')
        self.tree.heading('deposits', text='Ввод/вывод')
        self.tree.heading('assets', text='Активы')
        self.tree.heading('balance', text='Деньги')
        self.tree.heading('total', text='Итого')
        self.tree.heading('income', text='Доход')
        self.tree.heading('pct', text='Доходность %')

        self.tree.column('month', width=120, anchor=tk.CENTER)
        self.tree.column('deposits', width=130, anchor=tk.E)
        self.tree.column('assets', width=130, anchor=tk.E)
        self.tree.column('balance', width=130, anchor=tk.E)
        self.tree.column('total', width=130, anchor=tk.E)
        self.tree.column('income', width=130, anchor=tk.E)
        self.tree.column('pct', width=110, anchor=tk.E)

        scrollbar = tb.Scrollbar(
            table_frame, orient=tk.VERTICAL, command=self.tree.yview,
        )
        self.tree.configure(yscroll=scrollbar.set)
        self.tree.pack(side=tk.LEFT, fill=tk.X, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    # ── фильтры ──────────────────────────────────────────────────

    def _update_filters(self):
        current_year = str(datetime.now().year)
        from_val = self.year_from_combo.get() if self.year_from_combo['values'] else ''
        to_val = self.year_to_combo.get() if self.year_to_combo['values'] else ''

        years = get_snapshot_years()
        self.year_from_combo['values'] = years
        self.year_to_combo['values'] = years
        if not from_val or from_val not in years:
            from_val = current_year if current_year in years else years[0] if years else current_year
        if not to_val or to_val not in years:
            to_val = current_year if current_year in years else years[0] if years else current_year
        self.year_from_combo.set(from_val)
        self.year_to_combo.set(to_val)

    # ── обновление данных ────────────────────────────────────────

    def refresh(self):
        year_from = self.year_from_combo.get()
        year_to = self.year_to_combo.get()

        if int(year_from) > int(year_to):
            messagebox.showwarning("Неверный период", "Год «С» не может быть больше года «По»")
            return

        account_id = self._get_account_filter()

        rows, prev_total_rub = get_analysis_monthly(year_from, year_to, account_id)

        if not rows:
            self.ax.clear()
            self.ax.set_title("Нет данных")
            self.canvas.draw()
            try:
                for item in self.tree.get_children():
                    self.tree.delete(item)
            except Exception:
                pass
            return

        from matplotlib.transforms import blended_transform_factory

        months = []
        year_labels = []
        balances = []
        assets_vals = []
        totals = []

        for r in rows:
            ym = r["ym"]
            month_num = int(ym.split('-')[1])
            yr = ym[:4]
            months.append(MONTHS_RU[month_num - 1])
            year_labels.append(yr)
            balances.append(r["balance_rub"] or 0)
            assets_vals.append(r["assets_value_rub"] or 0)
            totals.append(r["portfolio_total_rub"] or 0)

        self.ax.clear()
        if year_from == year_to:
            chart_title = f"Динамика портфеля — {year_from} г."
        else:
            chart_title = f"Динамика портфеля — {year_from}–{year_to} г."
        self.ax.set_title(chart_title, fontsize=12)

        x = list(range(len(months)))
        self.ax.plot(x, totals, color='blue', linewidth=2, label='Итого портфель')
        self.ax.plot(x, assets_vals, color='green', linewidth=2, label='Активы')
        self.ax.plot(x, balances, color='orange', linewidth=2, label='Деньги на счету')

        unique_years = []
        for y in year_labels:
            if not unique_years or unique_years[-1] != y:
                unique_years.append(y)

        if len(unique_years) > 1:
            bt = blended_transform_factory(self.ax.transData, self.ax.transAxes)
            for i in range(len(months)):
                if year_labels[i] != unique_years[0] and year_labels[i] != year_labels[i - 1]:
                    px = i - 0.5
                    self.ax.axvline(x=px, color='gray', linewidth=0.8, alpha=0.7)
                    self.ax.text(px, -0.15, str(year_labels[i]),
                                 transform=bt, ha='center', va='top', fontsize=9,
                                 fontweight='bold')

        self.ax.set_xticks(list(x))
        self.ax.set_xticklabels(months, fontsize=8)
        self.ax.set_ylabel('Руб')
        self.ax.legend()
        self.ax.grid(True, alpha=0.3)
        self.ax.yaxis.set_major_formatter(FuncFormatter(_format_rub))
        self.ax.tick_params(axis='y', labelsize=7)
        self.figure.tight_layout()
        self.canvas.draw()

        try:
            for item in self.tree.get_children():
                self.tree.delete(item)
        except Exception:
            pass

        prev_total = prev_total_rub

        for r in rows:
            dep = r["deposits"] or 0
            wdr = r["withdrawals"] or 0
            assets = r["assets_value_rub"] or 0
            bal = r["balance_rub"] or 0
            total = r["portfolio_total_rub"] or 0

            month_num = int(r["ym"].split('-')[1])
            month_display = f"{MONTHS_RU[month_num - 1]} {r['ym'][:4]}"

            if prev_total is not None:
                income = total - (prev_total + dep - wdr)
                income_str = f"{income:+.2f}"
                base = prev_total + dep
                if base != 0:
                    pct = (income / base) * 100
                    pct_str = f"{pct:.2f}"
                else:
                    pct_str = "\u2014"
            else:
                income_str = "\u2014"
                pct_str = "\u2014"

            self.tree.insert('', tk.END, values=(
                month_display,
                f"{dep - wdr:.2f}",
                f"{assets:.2f}",
                f"{bal:.2f}",
                f"{total:.2f}",
                income_str,
                pct_str,
            ))

            prev_total = total

        apply_zebra(self.tree)


# ═══════════════════════════════════════════════════════════
#  Вкладка «Доходы»
# ═══════════════════════════════════════════════════════════

class IncomeTab(tb.Frame):
    """Вкладка доходов (купоны и дивиденды)."""

    def __init__(self, parent, controller=None):
        super().__init__(parent)
        self.controller = controller
        self._create_ui()
        self._update_filters()
        self.refresh()

    # ── получение ID счёта ──────────────────────────────────────

    def _get_account_filter(self):
        if self.controller and hasattr(self.controller, 'selected_account_id'):
            aid = self.controller.selected_account_id.get()
            if aid > 0:
                return aid
        return 'all'

    # ── создание интерфейса ──────────────────────────────────────

    def _create_ui(self):
        # Фильтры
        filter_frame = tb.Frame(self)
        filter_frame.pack(fill=tk.X, padx=10, pady=(10, 5))

        tb.Label(filter_frame, text="Период:").pack(side=tk.LEFT, padx=(0, 4))
        self.year_from_combo = tb.Combobox(
            filter_frame, state="readonly", width=8, justify=tk.CENTER,
        )
        self.year_from_combo.pack(side=tk.LEFT, padx=(0, 2))
        tb.Label(filter_frame, text="—").pack(side=tk.LEFT, padx=2)
        self.year_to_combo = tb.Combobox(
            filter_frame, state="readonly", width=8, justify=tk.CENTER,
        )
        self.year_to_combo.pack(side=tk.LEFT, padx=(0, 15))
        self.year_from_combo.bind('<<ComboboxSelected>>', lambda e: self.refresh())
        self.year_to_combo.bind('<<ComboboxSelected>>', lambda e: self.refresh())

        tb.Label(filter_frame, text="Тип:").pack(side=tk.LEFT, padx=(0, 4))
        self.type_combo = tb.Combobox(
            filter_frame, state="readonly", width=14,
        )
        self.type_combo.pack(side=tk.LEFT)
        self.type_combo['values'] = ['Все доходы', 'Купоны', 'Дивиденды']
        self.type_combo.set('Все доходы')
        self.type_combo.bind('<<ComboboxSelected>>', lambda e: self.refresh())

        # График
        chart_frame = tb.Frame(self)
        chart_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(5, 5))

        self.figure = Figure(figsize=(8, 4))
        self.ax = self.figure.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(self.figure, master=chart_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        # Таблица
        table_frame = tb.LabelFrame(self, text="Доходы", padx=5, pady=5)
        table_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        columns = ('month', 'ticker', 'name', 'tx_type', 'amount', 'currency', 'amount_rub', 'account')
        self.tree = tb.Treeview(table_frame, columns=columns, show='headings')

        self.tree.heading('month', text='Месяц')
        self.tree.heading('ticker', text='Тикер')
        self.tree.heading('name', text='Название актива')
        self.tree.heading('tx_type', text='Тип')
        self.tree.heading('amount', text='Сумма')
        self.tree.heading('currency', text='Валюта')
        self.tree.heading('amount_rub', text='Сумма в рублях')
        self.tree.heading('account', text='Счёт')

        self.tree.column('month', width=90, anchor=tk.CENTER)
        self.tree.column('ticker', width=90, anchor=tk.CENTER)
        self.tree.column('name', width=180)
        self.tree.column('tx_type', width=80, anchor=tk.CENTER)
        self.tree.column('amount', width=90, anchor=tk.E)
        self.tree.column('currency', width=60, anchor=tk.CENTER)
        self.tree.column('amount_rub', width=120, anchor=tk.E)
        self.tree.column('account', width=140)

        scrollbar = tb.Scrollbar(
            table_frame, orient=tk.VERTICAL, command=self.tree.yview,
        )
        self.tree.configure(yscroll=scrollbar.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    # ── фильтры ──────────────────────────────────────────────────

    def _update_filters(self):
        current_year = str(datetime.now().year)
        from_val = self.year_from_combo.get() if self.year_from_combo['values'] else ''
        to_val = self.year_to_combo.get() if self.year_to_combo['values'] else ''

        years = get_transaction_years()
        self.year_from_combo['values'] = years
        self.year_to_combo['values'] = years
        if not from_val or from_val not in years:
            from_val = current_year if current_year in years else years[0] if years else current_year
        if not to_val or to_val not in years:
            to_val = current_year if current_year in years else years[0] if years else current_year
        self.year_from_combo.set(from_val)
        self.year_to_combo.set(to_val)

    # ── обновление данных ────────────────────────────────────────

    def refresh(self):
        year_from = self.year_from_combo.get()
        year_to = self.year_to_combo.get()

        if int(year_from) > int(year_to):
            messagebox.showwarning("Неверный период", "Год «С» не может быть больше года «По»")
            return

        account_id = self._get_account_filter()
        type_filter = self.type_combo.get()

        rows = get_income_transactions(year_from, year_to, account_id, type_filter)
        totals = get_income_monthly_totals(year_from, year_to, account_id, type_filter)

        if not rows:
            self.ax.clear()
            self.ax.set_title("Нет данных")
            self.canvas.draw()
            try:
                for item in self.tree.get_children():
                    self.tree.delete(item)
            except Exception:
                pass
            return

        # ── Агрегация по месяцам для графика ──
        monthly = {}
        for r in totals:
            m = r["month"]
            if m not in monthly:
                monthly[m] = {'купон': 0.0, 'дивиденд': 0.0}
            rate = _rate_for_currency(r["currency_code"])
            val = (r["total_amount"] or 0) * rate
            monthly[m][r["tx_type"]] += val

        # Собираем отсортированные месяцы и названия
        sorted_months = sorted(monthly.keys())
        month_names = []
        year_labels = []
        coupons = []
        dividends = []

        for m in sorted_months:
            yr = m[:4]
            mn = int(m.split('-')[1])
            month_names.append(MONTHS_RU[mn - 1])
            year_labels.append(yr)
            coupons.append(monthly[m]['купон'])
            dividends.append(monthly[m]['дивиденд'])

        # ── График (stacked bar) ──
        self.ax.clear()
        if year_from == year_to:
            chart_title = f"Доходы — {year_from} г."
        else:
            chart_title = f"Доходы — {year_from}–{year_to} г."
        self.ax.set_title(chart_title, fontsize=12)

        x = list(range(len(month_names)))
        width = 0.6

        if type_filter == 'Все доходы':
            self.ax.bar(x, coupons, width, label='Купоны', color='orange')
            self.ax.bar(x, dividends, width, bottom=coupons, label='Дивиденды', color='green')
        elif type_filter == 'Купоны':
            self.ax.bar(x, coupons, width, label='Купоны', color='orange')
        elif type_filter == 'Дивиденды':
            self.ax.bar(x, dividends, width, label='Дивиденды', color='green')

        unique_years = []
        for y in year_labels:
            if not unique_years or unique_years[-1] != y:
                unique_years.append(y)

        if len(unique_years) > 1:
            bt = blended_transform_factory(self.ax.transData, self.ax.transAxes)
            for i in range(len(month_names)):
                if year_labels[i] != unique_years[0] and year_labels[i] != year_labels[i - 1]:
                    px = i - 0.5
                    self.ax.axvline(x=px, color='gray', linewidth=0.8, alpha=0.7)
                    self.ax.text(px, -0.15, str(year_labels[i]),
                                 transform=bt, ha='center', va='top', fontsize=9,
                                 fontweight='bold')

        self.ax.set_xticks(list(x))
        self.ax.set_xticklabels(month_names, rotation=45, ha='right')
        self.ax.set_ylabel('Руб')

        self.ax.legend(loc='upper left')

        self.ax.grid(True, alpha=0.3, axis='y')
        self.ax.yaxis.set_major_formatter(FuncFormatter(_format_rub))
        self.ax.tick_params(axis='y', labelsize=7)
        self.figure.tight_layout()
        self.canvas.draw()

        # ── Таблица ──
        try:
            for item in self.tree.get_children():
                self.tree.delete(item)
        except Exception:
            pass

        type_display = {'купон': 'Купон', 'дивиденд': 'Дивиденд'}

        for r in rows:
            amount = r["amount"] or 0
            currency = r["currency_code"] or "RUB"
            rate = _rate_for_currency(currency)
            amount_rub = amount * rate

            mn = int(r["month"].split('-')[1])
            month_display = f"{MONTHS_RU[mn - 1]} {r['month'][:4]}"

            self.tree.insert('', tk.END, values=(
                month_display,
                r["ticker"] or '',
                r["asset_name"] or '',
                type_display.get(r["tx_type"], r["tx_type"]),
                f"{amount:.2f}",
                currency,
                f"{amount_rub:.2f}",
                r["account_name"] or '',
            ))

        apply_zebra(self.tree)


# ═══════════════════════════════════════════════════════════
#  Вкладка «Эффективность»
# ═══════════════════════════════════════════════════════════

class DealsTab(tb.Frame):
    """Вкладка эффективности (покупки и продажи)."""

    def __init__(self, parent, controller=None):
        super().__init__(parent)
        self.controller = controller
        self._create_ui()
        self._update_filters()
        self.refresh()

    # ── получение ID счёта ──────────────────────────────────────

    def _get_account_filter(self):
        if self.controller and hasattr(self.controller, 'selected_account_id'):
            aid = self.controller.selected_account_id.get()
            if aid > 0:
                return aid
        return 'all'

    # ── создание интерфейса ──────────────────────────────────────

    def _create_ui(self):
        # Фильтры
        filter_frame = tb.Frame(self)
        filter_frame.pack(fill=tk.X, padx=10, pady=(10, 5))

        tb.Label(filter_frame, text="Период:").pack(side=tk.LEFT, padx=(0, 4))
        self.year_from_combo = tb.Combobox(
            filter_frame, state="readonly", width=8, justify=tk.CENTER,
        )
        self.year_from_combo.pack(side=tk.LEFT, padx=(0, 2))
        tb.Label(filter_frame, text="—").pack(side=tk.LEFT, padx=2)
        self.year_to_combo = tb.Combobox(
            filter_frame, state="readonly", width=8, justify=tk.CENTER,
        )
        self.year_to_combo.pack(side=tk.LEFT, padx=(0, 15))
        self.year_from_combo.bind('<<ComboboxSelected>>', lambda e: self.refresh())
        self.year_to_combo.bind('<<ComboboxSelected>>', lambda e: self.refresh())

        tb.Label(filter_frame, text="Тип:").pack(side=tk.LEFT, padx=(0, 4))
        self.type_combo = tb.Combobox(
            filter_frame, state="readonly", width=12,
        )
        self.type_combo.pack(side=tk.LEFT, padx=(0, 10))
        self.type_combo['values'] = ['Все', 'Покупки', 'Продажи']
        self.type_combo.set('Все')
        self.type_combo.bind('<<ComboboxSelected>>', lambda e: self.refresh())

        # График
        chart_frame = tb.Frame(self)
        chart_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(5, 5))

        self.figure = Figure(figsize=(8, 4))
        self.ax = self.figure.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(self.figure, master=chart_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        # Таблица
        table_frame = tb.LabelFrame(self, text="Эффективность", padx=5, pady=5)
        table_frame.pack(fill=tk.X, padx=10, pady=(0, 5))

        columns = ('date', 'ticker', 'name', 'tx_type', 'qty', 'price',
                   'amount', 'currency', 'amount_rub', 'account', 'profit_loss')
        self.tree = tb.Treeview(table_frame, columns=columns, show='headings')

        self.tree.heading('date', text='Дата', command=lambda: self._sort_column('date', False))
        self.tree.heading('ticker', text='Тикер', command=lambda: self._sort_column('ticker', False))
        self.tree.heading('name', text='Название актива', command=lambda: self._sort_column('name', False))
        self.tree.heading('tx_type', text='Тип', command=lambda: self._sort_column('tx_type', False))
        self.tree.heading('qty', text='Количество', command=lambda: self._sort_column('qty', False))
        self.tree.heading('price', text='Цена', command=lambda: self._sort_column('price', False))
        self.tree.heading('amount', text='Сумма', command=lambda: self._sort_column('amount', False))
        self.tree.heading('currency', text='Валюта', command=lambda: self._sort_column('currency', False))
        self.tree.heading('amount_rub', text='Сумма в рублях', command=lambda: self._sort_column('amount_rub', False))
        self.tree.heading('account', text='Счёт', command=lambda: self._sort_column('account', False))
        self.tree.heading('profit_loss', text='Прибыль/убыток', command=lambda: self._sort_column('profit_loss', False))

        self.tree.column('date', width=100, anchor=tk.CENTER)
        self.tree.column('ticker', width=90, anchor=tk.CENTER)
        self.tree.column('name', width=180)
        self.tree.column('tx_type', width=80, anchor=tk.CENTER)
        self.tree.column('qty', width=80, anchor=tk.E)
        self.tree.column('price', width=90, anchor=tk.E)
        self.tree.column('amount', width=100, anchor=tk.E)
        self.tree.column('currency', width=60, anchor=tk.CENTER)
        self.tree.column('amount_rub', width=120, anchor=tk.E)
        self.tree.column('account', width=140)
        self.tree.column('profit_loss', width=120, anchor=tk.E)

        scrollbar = tb.Scrollbar(
            table_frame, orient=tk.VERTICAL, command=self.tree.yview,
        )
        self.tree.configure(yscroll=scrollbar.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Итоговая строка
        self.total_label = tb.Label(
            self, text='Всего покупок: 0 | Всего продаж: 0 | Прибыль/убыток от продаж: 0.00 ₽',
            padding=5,
        )
        self.total_label.pack(fill=tk.X, padx=10, pady=(0, 10))

        # Сообщения
        self._empty_msg = None

    # ── сортировка ───────────────────────────────────────────────

    def _sort_column(self, col, reverse):
        """Сортировка по столбцу при клике на заголовок."""
        items = [(self.tree.set(k, col), k) for k in self.tree.get_children()]
        try:
            items.sort(key=lambda x: float(x[0]) if x[0].replace('.', '', 1).replace(',', '').replace('-', '').isdigit() else x[0], reverse=reverse)
        except ValueError:
            items.sort(reverse=reverse)
        for idx, (_, item) in enumerate(items):
            self.tree.move(item, '', idx)
        self.tree.heading(col, command=lambda: self._sort_column(col, not reverse))

    # ── фильтры ──────────────────────────────────────────────────

    def _update_filters(self):
        current_year = str(datetime.now().year)
        from_val = self.year_from_combo.get() if self.year_from_combo['values'] else ''
        to_val = self.year_to_combo.get() if self.year_to_combo['values'] else ''

        years = get_transaction_years()
        self.year_from_combo['values'] = years
        self.year_to_combo['values'] = years
        if not from_val or from_val not in years:
            from_val = current_year if current_year in years else years[0] if years else current_year
        if not to_val or to_val not in years:
            to_val = current_year if current_year in years else years[0] if years else current_year
        self.year_from_combo.set(from_val)
        self.year_to_combo.set(to_val)

    # ── обновление данных ────────────────────────────────────────

    def refresh(self):
        year_from = self.year_from_combo.get()
        year_to = self.year_to_combo.get()

        if int(year_from) > int(year_to):
            messagebox.showwarning("Неверный период", "Год «С» не может быть больше года «По»")
            return

        account_id = self._get_account_filter()
        type_filter = self.type_combo.get()

        rows = get_deal_transactions(year_from, year_to, account_id, type_filter)

        # ── Агрегация прибыли по месяцам ──
        monthly_profits = {}
        for r in rows:
            if r["tx_type"] == 'продажа' and r["profit"] is not None:
                currency = r["currency_code"] or "RUB"
                rate = _rate_for_currency(currency)
                profit_rub = r["profit"] * rate
                month = r["date"][:7]
                monthly_profits[month] = monthly_profits.get(month, 0.0) + profit_rub

        sorted_months = sorted(monthly_profits.keys())
        month_names = []
        year_labels = []
        profit_values = []

        for m in sorted_months:
            yr = m[:4]
            mn = int(m.split('-')[1])
            month_names.append(MONTHS_RU[mn - 1])
            year_labels.append(yr)
            profit_values.append(monthly_profits[m])

        # ── График ──
        self.ax.clear()
        if month_names:
            if year_from == year_to:
                chart_title = f"Эффективность — {year_from} г."
            else:
                chart_title = f"Эффективность — {year_from}–{year_to} г."
            self.ax.set_title(chart_title, fontsize=12)

            x = list(range(len(month_names)))
            profit_x = [i for i, p in enumerate(profit_values) if p >= 0]
            profit_y = [profit_values[i] for i in profit_x]
            loss_x = [i for i, p in enumerate(profit_values) if p < 0]
            loss_y = [profit_values[i] for i in loss_x]
            if profit_x:
                self.ax.bar(profit_x, profit_y, color='green', label='Прибыль')
            if loss_x:
                self.ax.bar(loss_x, loss_y, color='red', label='Убыток')
            self.ax.axhline(y=0, color='black', linewidth=0.5)

            unique_years = []
            for y in year_labels:
                if not unique_years or unique_years[-1] != y:
                    unique_years.append(y)

            if len(unique_years) > 1:
                bt = blended_transform_factory(self.ax.transData, self.ax.transAxes)
                for i in range(len(month_names)):
                    if year_labels[i] != unique_years[0] and year_labels[i] != year_labels[i - 1]:
                        px = i - 0.5
                        self.ax.axvline(x=px, color='gray', linewidth=0.8, alpha=0.7)
                        self.ax.text(px, -0.15, str(year_labels[i]),
                                     transform=bt, ha='center', va='top', fontsize=9,
                                     fontweight='bold')

            self.ax.set_xticks(list(x))
            self.ax.set_xticklabels(month_names, rotation=45, ha='right')
            self.ax.set_ylabel('Руб')

            self.ax.legend(loc='upper left')
            self.ax.grid(True, alpha=0.3, axis='y')
            self.ax.yaxis.set_major_formatter(FuncFormatter(_format_rub))
            self.ax.tick_params(axis='y', labelsize=7)
        else:
            self.ax.set_title("Нет данных о продажах")
        self.figure.tight_layout()
        self.canvas.draw()

        # ── Очистка таблицы ──
        try:
            for item in self.tree.get_children():
                self.tree.delete(item)
        except Exception:
            pass

        if self._empty_msg:
            self._empty_msg.destroy()
            self._empty_msg = None

        if not rows:
            self._empty_msg = tb.Label(
                self.tree, text="Нет сделок за выбранный период",
                padding=20,
            )
            self._empty_msg.pack()
            self.total_label.configure(text='Всего покупок: 0 | Всего продаж: 0 | Прибыль/убыток от продаж: 0.00 ₽')
            return

        buy_total_rub = 0.0
        sell_total_rub = 0.0
        total_profit = 0.0

        type_display = {'покупка': 'Покупка', 'продажа': 'Продажа'}

        for r in rows:
            qty = r["qty"] or 0
            price = r["price"] or 0
            amount = qty * price
            currency = r["currency_code"] or "RUB"
            rate = _rate_for_currency(currency)
            amount_rub = amount * rate
            profit_loss = r["profit"]

            tx = r["tx_type"]
            if tx == 'покупка':
                buy_total_rub += amount_rub
            elif tx == 'продажа':
                sell_total_rub += amount_rub
                if profit_loss is not None:
                    total_profit += profit_loss

            pl_text = ""
            if tx == 'продажа' and profit_loss is not None:
                if profit_loss >= 0:
                    pl_text = f"+{profit_loss:.2f}"
                    self.tree.insert('', tk.END, values=(
                        r["date"],
                        r["ticker"],
                        r["asset_name"] or '',
                        type_display.get(tx, tx),
                        f"{qty:.4f}" if qty != int(qty) else str(int(qty)),
                        f"{price:.2f}",
                        f"{amount:.2f}",
                        currency,
                        f"{amount_rub:.2f}",
                        r["account_name"] or '',
                        pl_text,
                    ), tags=('profit_pos',))
                else:
                    pl_text = f"{profit_loss:.2f}"
                    self.tree.insert('', tk.END, values=(
                        r["date"],
                        r["ticker"],
                        r["asset_name"] or '',
                        type_display.get(tx, tx),
                        f"{qty:.4f}" if qty != int(qty) else str(int(qty)),
                        f"{price:.2f}",
                        f"{amount:.2f}",
                        currency,
                        f"{amount_rub:.2f}",
                        r["account_name"] or '',
                        pl_text,
                    ), tags=('profit_neg',))
            else:
                pl_text = "—"
                self.tree.insert('', tk.END, values=(
                    r["date"],
                    r["ticker"],
                    r["asset_name"] or '',
                    type_display.get(tx, tx),
                    f"{qty:.4f}" if qty != int(qty) else str(int(qty)),
                    f"{price:.2f}",
                    f"{amount:.2f}",
                    currency,
                    f"{amount_rub:.2f}",
                    r["account_name"] or '',
                    pl_text,
                ))

        self.total_label.configure(
            text=f'Всего покупок: {buy_total_rub:.2f} ₽ | Всего продаж: {sell_total_rub:.2f} ₽ | Прибыль/убыток от продаж: {total_profit:+.2f} ₽'
        )

        # Стилизация тегов
        try:
            self.tree.tag_configure('profit_pos', foreground='green')
            self.tree.tag_configure('profit_neg', foreground='red')
        except Exception:
            pass

        apply_zebra(self.tree)


# ═══════════════════════════════════════════════════════════
#  AnalysisView — корневая вкладка с Notebook
# ═══════════════════════════════════════════════════════════

class AnalysisView(tb.Frame):
    """Вкладка анализа доходности портфеля."""

    def __init__(self, parent, controller=None):
        super().__init__(parent)
        self.controller = controller
        notebook = tb.Notebook(self)
        notebook.pack(fill=tk.BOTH, expand=True, padx=0, pady=0)

        self.dynamics_tab = DynamicsTab(notebook, controller=self.controller)
        self.income_tab = IncomeTab(notebook, controller=self.controller)
        self.deals_tab = DealsTab(notebook, controller=self.controller)

        notebook.add(self.dynamics_tab, text="Динамика портфеля")
        notebook.add(self.income_tab, text="Доходы")
        notebook.add(self.deals_tab, text="Эффективность")

        self.notebook = notebook

        # При переключении вкладок обновляем данные
        self.notebook.bind('<<NotebookTabChanged>>', lambda e: self._on_tab_change())

    def _on_tab_change(self):
        """Обновить данные при переключении вкладки."""
        tab_id = self.notebook.index(self.notebook.select())
        tabs = [self.dynamics_tab, self.income_tab, self.deals_tab]
        if tab_id == 0:
            self.dynamics_tab.refresh()
        elif tab_id == 1:
            self.income_tab.refresh()
        elif tab_id == 2:
            self.deals_tab.refresh()

    def refresh(self):
        """Обновить все вкладки."""
        self.dynamics_tab.refresh()
        self.income_tab.refresh()
        self.deals_tab.refresh()

import tkinter as tk
import ttkbootstrap as tb
from tkinter import messagebox
from database import (
    get_all_accounts, get_account,
    get_snapshots, get_snapshot_assets
)
from table_utils import apply_zebra


class CutsView(tb.Frame):
    """Вкладка 'Срезы' — управление снимками портфеля по счёту."""

    def __init__(self, parent, controller=None):
        super().__init__(parent)
        self.controller = controller
        self._create_ui()
        self.refresh()

    # ── получение ID счёта ──────────────────────────────────────
    def _get_account_id_from_controller(self):
        """Получить ID счёта из главного окна."""
        if self.controller and hasattr(self.controller, 'selected_account_id'):
            aid = self.controller.selected_account_id.get()
            if aid > 0:
                return aid
        return None

    def _get_active_account_id(self):
        """Получить активный ID счёта."""
        return self._get_account_id_from_controller()

    # ── создание интерфейса ──────────────────────────────────────
    def _create_ui(self):
        # ── верхний фрейм: таблица срезов ──
        top_frame = tb.LabelFrame(self, text="Срезы", padx=5, pady=5)
        top_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=(5, 0))

        columns = ("date", "year_month", "account", "balance", "total", "cut_id")
        self.tree = tb.Treeview(top_frame, columns=columns, show="headings", height=12)

        self.tree.heading("date", text="Дата")
        self.tree.heading("year_month", text="Год-месяц")
        self.tree.heading("account", text="Счёт")
        self.tree.heading("balance", text="На счету (₽)")
        self.tree.heading("total", text="Итого (₽)")

        self.tree.column("date", width=120, anchor=tk.CENTER)
        self.tree.column("year_month", width=120, anchor=tk.CENTER)
        self.tree.column("account", width=180)
        self.tree.column("balance", width=120, anchor=tk.E)
        self.tree.column("total", width=120, anchor=tk.E)
        self.tree.column("cut_id", width=0, stretch=False, minwidth=0)

        scrollbar = tb.Scrollbar(top_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscroll=scrollbar.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        # Кнопки управления срезом
        cut_btn_frame = tb.Frame(top_frame)
        cut_btn_frame.pack(fill=tk.X, pady=(5, 0))

        tb.Button(cut_btn_frame, text="Сохранить срез", command=self._save_cut, bootstyle="success").pack(side=tk.TOP, fill=tk.X, pady=(0, 2))
        tb.Button(cut_btn_frame, text="Удалить срез", command=self._delete_cut, bootstyle="danger").pack(side=tk.TOP, fill=tk.X, pady=(2, 0))

        # ── нижний фрейм: детали среза ──
        bottom_frame = tb.LabelFrame(self, text="Детали среза", padx=5, pady=5)
        bottom_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=(5, 0))

        detail_columns = ("asset", "quantity", "price", "total")
        self.detail_tree = tb.Treeview(bottom_frame, columns=detail_columns, show="headings", height=10)

        self.detail_tree.heading("asset", text="Актив")
        self.detail_tree.heading("quantity", text="Количество")
        self.detail_tree.heading("price", text="Цена")
        self.detail_tree.heading("total", text="Стоимость")

        self.detail_tree.column("asset", width=200)
        self.detail_tree.column("quantity", width=100, anchor=tk.CENTER)
        self.detail_tree.column("price", width=100, anchor=tk.E)
        self.detail_tree.column("total", width=120, anchor=tk.E)

        detail_scrollbar = tb.Scrollbar(bottom_frame, orient=tk.VERTICAL, command=self.detail_tree.yview)
        self.detail_tree.configure(yscroll=detail_scrollbar.set)
        self.detail_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        detail_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Статус-бар
        self.status_var = tk.StringVar(value="Готово")
        status_bar = tb.Label(self, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W)
        status_bar.pack(fill=tk.X, side=tk.BOTTOM)

        # Словарь для хранения данных срезов по их unique ID
        self._cuts_data = {}
        self._cut_counter = 0

    # ── данные ────────────────────────────────────────────────────
    def _get_all_cuts(self):
        """Получить список срезов из базы данных."""
        return get_snapshots()

    # ── обновление таблицы ────────────────────────────────────────
    def refresh(self):
        """Обновить таблицу срезов и детали."""
        try:
            children = self.tree.tk.call(self.tree._w, "children", "")
        except Exception:
            return

        for item in children:
            self.tree.delete(item)

        # Очищаем кэш данных
        self._cuts_data = {}
        self._cut_counter = 0

        cuts = self._get_all_cuts()
        aid = self._get_active_account_id()
        if aid is not None:
            cuts = [c for c in cuts if c["account_id"] == aid]

        if not cuts:
            self.status_var.set("Срезов нет")
            self._clear_details()
            return

        for cut in cuts:
            cut_id = self._cut_counter
            self._cuts_data[str(cut_id)] = dict(cut)
            self._cut_counter += 1

            self.tree.insert('', tk.END, values=(
                cut["date"],
                cut["date"][:7],
                cut["account_name"] or "Не указан",
                f"{cut['balance_rub']:.2f}",
                f"{cut['portfolio_total_rub']:.2f}",
                cut_id,
            ), tags=(str(cut_id),))

        self.status_var.set(f"Срезов: {len(cuts)}")
        apply_zebra(self.tree)

        try:
            detail_children = self.detail_tree.tk.call(self.detail_tree._w, "children", "")
            if not detail_children:
                self._clear_details()
        except Exception:
            pass

    def _clear_details(self):
        """Очистить таблицу деталей."""
        try:
            children = self.detail_tree.tk.call(self.detail_tree._w, "children", "")
        except Exception:
            return
        for item in children:
            self.detail_tree.delete(item)

    # ── выбор среза ───────────────────────────────────────────────
    def _on_select(self, event):
        """При выборе среза — показать детали."""
        selected = self.tree.selection()
        if not selected:
            self._clear_details()
            return

        item = self.tree.item(selected[0])
        values = item["values"]
        cut_id = str(values[5]) if len(values) > 5 else None

        cut_data = self._cuts_data.get(cut_id) if cut_id is not None else None
        if cut_data is None:
            self._clear_details()
            return

        self._clear_details()
        snapshot_id = cut_data["id"]
        assets = get_snapshot_assets(snapshot_id)

        for asset in assets:
            name = asset["name"] or asset["ticker"]
            self.detail_tree.insert('', tk.END, values=(
                name,
                f"{asset['quantity']:.4f}",
                f"{asset['current_price']:.2f}",
                f"{asset['value_rub']:.2f}",
            ))

        apply_zebra(self.detail_tree)

    # ── сохранение среза ──────────────────────────────────────────
    def _save_cut(self):
        """Сохранить срез портфеля."""
        from snapshot_guard import save_snapshot_full
        result = save_snapshot_full(self, self.status_var)
        if result is None:
            return  # отменено
        accounts_count, total_portfolio = result
        self.refresh()

    # ── удаление среза ────────────────────────────────────────────
    def _delete_cut(self):
        """Удалить выбранный срез."""
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("Внимание", "Выберите срез для удаления")
            return

        item = self.tree.item(selected[0])
        values = item["values"]
        cut_id = str(values[5]) if len(values) > 5 else None

        cut_data = self._cuts_data.get(cut_id)
        if cut_data is None:
            messagebox.showerror("Ошибка", "Не удалось определить срез для удаления")
            return

        snapshot_id = cut_data.get("id")
        if not snapshot_id:
            messagebox.showerror("Ошибка", "Не удалось определить срез для удаления")
            return

        if not messagebox.askyesno("Подтверждение",
                                    f"Удалить срез\n{cut_data['account_name']} от {cut_data['date']}?"):
            return

        self._remove_cut_from_db(snapshot_id, cut_data)
        messagebox.showinfo("Успех", "Срез удалён")
        self.refresh()

    def _remove_cut_from_db(self, snapshot_id, cut_data):
        """Удалить срез и все его детали из БД (ON DELETE CASCADE)."""
        from database import get_connection
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM snapshots WHERE id = ?", (snapshot_id,))
        conn.commit()
        conn.close()

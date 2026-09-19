import re

import tkinter as tk
import ttkbootstrap as tb


class CalcPanel(tb.Labelframe):
    """Встроенный мини-калькулятор для поля суммы.

    Раскрывается кнопкой «±» прямо в диалоге: вводится базовая сумма,
    затем операции (+ − × ÷) и «=». «ОК» записывает результат в поле.
    """

    def __init__(self, master, entry, anchor=None, grid_pos=None):
        super().__init__(master, text="Калькулятор", padding=6)
        self.entry = entry
        self.dialog = master
        self._anchor = anchor      # фрейм-строка поля: панель вставляется сразу после него
        self._grid_pos = grid_pos  # параметры grid для диалогов с grid-раскладкой
        self._shown = False
        self._saved_geometry = None  # исходный размер диалога до раскрытия панели

        self._display = tk.StringVar(value="0")
        self._acc = None          # накопленное значение
        self._op = None           # ожидающая операция: + - * /
        self._new_operand = True  # следующая цифра начинает новое число

        display = tb.Entry(self, textvariable=self._display, justify=tk.RIGHT,
                           font=("TkDefaultFont", 12, "bold"))
        display.grid(row=0, column=0, columnspan=4, sticky="ew", padx=2, pady=(0, 4))
        display.bind("<Key>", self._on_key)
        self._display_widget = display

        pad = dict(padx=1, pady=1, sticky="nsew")
        tb.Button(self, text="C", width=4, bootstyle="danger-outline",
                  command=self._press(self._clear)).grid(row=1, column=0, **pad)
        tb.Button(self, text="⌫", width=4, bootstyle="secondary-outline",
                  command=self._press(self._backspace)).grid(row=1, column=1, **pad)
        tb.Button(self, text="÷", width=4, bootstyle="secondary",
                  command=self._press(lambda: self._set_op("/"))).grid(row=1, column=2, **pad)
        tb.Button(self, text="×", width=4, bootstyle="secondary",
                  command=self._press(lambda: self._set_op("*"))).grid(row=1, column=3, **pad)

        digits = [("7", "8", "9"), ("4", "5", "6"), ("1", "2", "3")]
        for r, row_digits in enumerate(digits, start=2):
            for c, d in enumerate(row_digits):
                tb.Button(self, text=d, width=4,
                          command=self._press(lambda d=d: self._digit(d))).grid(row=r, column=c, **pad)

        tb.Button(self, text="−", width=4, bootstyle="secondary",
                  command=self._press(lambda: self._set_op("-"))).grid(row=2, column=3, **pad)
        tb.Button(self, text="+", width=4, bootstyle="secondary",
                  command=self._press(lambda: self._set_op("+"))).grid(row=3, column=3, **pad)
        tb.Button(self, text="=", width=4, bootstyle="success",
                  command=self._press(self._equals)).grid(row=4, column=3, rowspan=2, **pad)
        tb.Button(self, text="0", width=9,
                  command=self._press(lambda: self._digit("0"))).grid(row=5, column=0, columnspan=2, **pad)
        tb.Button(self, text=".", width=4,
                  command=self._press(lambda: self._digit("."))).grid(row=5, column=2, **pad)

        btns = tb.Frame(self)
        btns.grid(row=6, column=0, columnspan=4, sticky="ew", padx=2, pady=(4, 2))
        tb.Button(btns, text="ОК", bootstyle="success",
                  command=self._apply).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=2)
        tb.Button(btns, text="Отмена", bootstyle="secondary",
                  command=self._press(self.hide)).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=2)

        tb.Label(self, text="Enter — «=» / применить, Esc — закрыть",
                 bootstyle="secondary").grid(row=7, column=0, columnspan=4, pady=(2, 0))

    def _press(self, fn):
        """Обёртка команды кнопки: выполнить и вернуть фокус на дисплей."""
        def run():
            try:
                fn()
            finally:
                try:
                    if self._display_widget.winfo_ismapped():
                        self._display_widget.focus_set()
                except tk.TclError:
                    pass
        return run

    def toggle(self):
        if self._shown:
            self.hide()
        else:
            self.show()

    def show(self):
        if not self._shown:
            try:
                self._saved_geometry = self.dialog.geometry()
            except tk.TclError:
                self._saved_geometry = None
            if self._grid_pos:
                self.grid(**self._grid_pos)
            else:
                nxt = self._next_packed_sibling()
                if nxt is not None:
                    self.pack(anchor="w", padx=10, pady=(0, 5), before=nxt)
                else:
                    self.pack(anchor="w", padx=10, pady=(0, 5))
            self._shown = True
            self._clear()
        self._fit_dialog()
        self._display_widget.focus_set()

    def hide(self):
        if not self._shown:
            return
        if self._grid_pos:
            self.grid_remove()
        else:
            self.pack_forget()
        self._shown = False
        # вернуть диалогу исходный размер (только WxH, без позиции)
        try:
            self.dialog.update_idletasks()
            m = re.match(r"(\d+x\d+)", self._saved_geometry or "")
            if m:
                self.dialog.geometry(m.group(1))
            else:
                self.dialog.geometry("")
        except tk.TclError:
            pass
        try:
            self.entry.focus_set()
        except tk.TclError:
            pass

    def _fit_dialog(self):
        # увеличить диалог, если содержимое перестало помещаться (не сжимать)
        try:
            self.dialog.update_idletasks()
            w = max(self.dialog.winfo_reqwidth(), self.dialog.winfo_width())
            h = max(self.dialog.winfo_reqheight(), self.dialog.winfo_height())
            self.dialog.geometry(f"{w}x{h}")
        except tk.TclError:
            pass

    def _next_packed_sibling(self):
        """Первый pack-виджет диалога, идущий после строки поля."""
        try:
            sibs = self.dialog.winfo_children()
            idx = sibs.index(self._anchor)
        except (ValueError, tk.TclError):
            return None
        for w in sibs[idx + 1:]:
            try:
                if w.winfo_manager() == "pack":
                    return w
            except tk.TclError:
                continue
        return None

    def _digit(self, d):
        if self._new_operand:
            self._display.set("")
            self._new_operand = False
        cur = self._display.get()
        if d == ".":
            if "." not in cur:
                self._display.set((cur or "0") + ".")
        elif cur == "0":
            self._display.set(d)
        elif cur == "-0":
            self._display.set("-" + d)
        else:
            self._display.set(cur + d)

    def _set_op(self, op):
        value = self._read_display()
        if value is None:
            return
        if self._op is not None and not self._new_operand:
            result = self._compute(self._acc, value, self._op)
            if result is None:
                self._show_error()
                return
            self._acc = result
            self._display.set(self._format(result))
        else:
            self._acc = value
        self._op = op
        self._new_operand = True

    def _equals(self):
        value = self._read_display()
        if value is None:
            return
        if self._op is not None:
            result = self._compute(self._acc, value, self._op)
            if result is None:
                self._show_error()
                return
            self._display.set(self._format(result))
            self._acc = result
            self._op = None
        self._new_operand = True

    def _clear(self):
        self._display.set("0")
        self._acc = None
        self._op = None
        self._new_operand = True

    def _backspace(self):
        if self._new_operand:
            return
        cur = self._display.get()
        cur = cur[:-1]
        if cur in ("", "-"):
            cur = "0"
        self._display.set(cur)

    def _read_display(self):
        try:
            return float(self._display.get().strip().replace(",", "."))
        except ValueError:
            return None

    def _compute(self, a, b, op):
        try:
            if op == "+":
                return a + b
            if op == "-":
                return a - b
            if op == "*":
                return a * b
            if op == "/":
                if b == 0:
                    return None
                return a / b
        except Exception:
            return None
        return None

    def _show_error(self):
        self._display.set("Ошибка")
        self._acc = None
        self._op = None
        self._new_operand = True

    def _format(self, value):
        value = round(value, 6)
        if abs(value - round(value)) < 1e-9:
            return str(int(round(value)))
        return f"{value:.6f}".rstrip("0").rstrip(".")

    def _apply(self):
        if self._op is not None:
            self._equals()
        value = self._read_display()
        if value is None:
            return
        self.entry.delete(0, tk.END)
        self.entry.insert(0, self._format(value))
        self.hide()

    def _on_key(self, event):
        keysym = event.keysym
        ch = event.char

        if keysym in ("Return", "KP_Enter"):
            if self._op is not None:
                self._equals()
            else:
                self._apply()
            return "break"
        if keysym == "Escape":
            self.hide()
            return "break"
        if keysym == "BackSpace":
            self._backspace()
            return "break"
        if not ch:
            return None

        if ch.isdigit():
            self._digit(ch)
            return "break"
        if ch in (".", ","):
            self._digit(".")
            return "break"
        if ch == "-":
            if self._new_operand:
                self._display.set("-")
                self._new_operand = False
            else:
                self._set_op("-")
            return "break"
        if ch == "+":
            self._set_op("+")
            return "break"
        if ch == "*":
            self._set_op("*")
            return "break"
        if ch == "/":
            self._set_op("/")
            return "break"

        # навигация и сочетания с Ctrl — как обычно, остальной ввод игнорируем
        if event.state & 0x0004 or keysym in ("Left", "Right", "Home", "End"):
            return None
        if ch.isprintable():
            return "break"
        return None


def attach_calc_button(dialog, entry, container=None, row=None, column=None):
    """Кнопка «±» рядом с полем суммы: раскрывает встроенный калькулятор.

    Для grid-диалога передаются row/column; для pack-диалога — container
    (фрейм, в котором поле и кнопка лежат в одну строку).
    """
    if row is not None:
        panel = CalcPanel(dialog, entry,
                          grid_pos=dict(row=row, column=(column or 1) + 1,
                                        columnspan=1, sticky="ew", padx=(0, 10), pady=5))
        parent = dialog
    else:
        anchor = container if container is not None else entry.master
        panel = CalcPanel(dialog, entry, anchor=anchor)
        parent = anchor

    btn = tb.Button(parent, text="±", width=3, bootstyle="secondary-outline",
                    command=panel.toggle)
    if row is not None:
        btn.grid(row=row, column=column, padx=(4, 10), pady=5, sticky=tk.W)
    else:
        btn.pack(side=tk.LEFT, padx=(4, 0))
    return btn

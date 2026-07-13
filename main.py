import tkinter as tk
import ttkbootstrap as tb
import sys
import os
import subprocess
import zipfile
import glob
import shutil
from datetime import datetime
from database import get_all_accounts, set_db_path, get_db_path
from db_schema import init_schema
from app_config import get_db_path as config_db_path
from version import APP_NAME_RU, APP_VERSION
from accounts_view import AccountsView
from assets_view import AssetsView
from transactions_view import TransactionsView

from analysis_view import AnalysisView
from settings_view import SettingsView
from cuts_view import CutsView

if getattr(sys, 'frozen', False):
    _APP_DIR = os.path.dirname(sys.executable)
else:
    _APP_DIR = os.path.dirname(os.path.abspath(__file__))


def bootstrap_db():
    """Определить путь к БД при запуске."""
    cfg_path = config_db_path()
    if cfg_path and os.path.exists(cfg_path):
        set_db_path(cfg_path)
    else:
        default_path = os.path.join(_APP_DIR, "investments.db")
        set_db_path(default_path)
        from app_config import set_db_path as cfg_set
        cfg_set(default_path)


def on_close(root):
    """Обработчик закрытия окна — сохранить путь к БД."""
    from app_config import set_db_path as cfg_set
    cfg_set(get_db_path())
    _do_archive()
    root.destroy()


def _do_archive():
    """Создать архив БД и удалить лишние (по настройкам app_config.json)."""
    from app_config import get_archive_settings
    settings = get_archive_settings()
    if not settings.get("enabled"):
        return
    folder = settings.get("folder")
    count = settings.get("count", 3)
    if not folder:
        return
    db_path = get_db_path()
    if not os.path.exists(db_path):
        return
    os.makedirs(folder, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    zip_path = os.path.join(folder, f"investments_{ts}.zip")
    try:
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.write(db_path, arcname=os.path.basename(db_path))
    except Exception:
        return
    archives = sorted(glob.glob(os.path.join(folder, "investments_*.zip")), key=os.path.getmtime)
    while len(archives) > count:
        try:
            os.remove(archives.pop(0))
        except OSError:
            break


class InvestmentApp:
    """Главное приложение для учёта инвестиций."""

    def __init__(self, root):
        self.root = root
        self.root.title(f"{APP_NAME_RU} {APP_VERSION}")
        self.root.geometry("1200x700")

        # Текущий выбранный счёт (0 = все)
        self.selected_account_id = tk.IntVar(value=0)

        # Настройка стилей
        self.setup_styles()

        # Создание макета
        self.create_layout()

    def setup_styles(self):
        """Настройка стилей tb."""
        style = tb.Style()

        style.configure('Title.TLabel', font=('Arial', 16, 'bold'))
        style.configure('Menu.TLabel', font=('Arial', 12, 'bold'))

        # Серая шапка таблиц. Рамку не рисуем (relief=flat) — иначе её дублируют
        # сразу два элемента (border + padding) и линии получаются толстыми.
        style.configure('Treeview.Heading',
                        background='#e8e8e8',
                        foreground='black',
                        relief='flat',
                        borderwidth=0,
                        font=('Arial', 10, 'bold'))
        # Шапка не меняет цвет при наведении — остаётся серой
        style.map('Treeview.Heading',
                  background=[('active', '#e8e8e8')],
                  foreground=[('active', 'black')])
        # Тонкие (1px) линии-разделители в шапке: снизу и между колонками.
        # Вертикальную линию делаем через image-элемент 1×1: штатные сепаратор-
        # элементы Tk всегда горизонтальны вне виджета Separator, а изображение,
        # растянутое по вертикали, даёт ровный 1px-разделитель нужного цвета.
        self._vline_img = tk.PhotoImage(width=1, height=1)
        self._vline_img.put("{#8b8b8b}", (0, 0))
        style.element_create('Heading.vline', 'image', self._vline_img)
        style.layout('Treeview.Heading', [
            ('Treeheading.cell', {'sticky': 'nswe'}),
            ('Treeheading.border', {
                'sticky': 'nswe',
                'children': [('Treeheading.padding', {
                    'sticky': 'nswe',
                    'children': [
                        ('Treeheading.image', {'side': 'right', 'sticky': ''}),
                        ('Treeheading.text', {'sticky': 'we'}),
                    ]
                })]
            }),
            ('Separator.separator', {'side': 'bottom', 'sticky': 'ew'}),
            ('Heading.vline', {'side': 'right', 'sticky': 'ns'}),
        ])

        # Белое тело таблиц
        style.configure('Treeview',
                        background='white',
                        fieldbackground='white',
                        foreground='black',
                        bordercolor='#cccccc',
                        rowheight=22)

    def create_layout(self):
        """Создание макета окна."""
        main_frame = tb.Frame(self.root)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Левое меню — тёмное
        left_menu = tb.Frame(main_frame, width=220, padding=10, bootstyle="dark")
        left_menu.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))

        # Заголовок — светлый текст на тёмном фоне
        title_label = tb.Label(left_menu, text=APP_NAME_RU,
                                bootstyle="inverse-dark",
                                font=('Arial', 16, 'bold'))
        title_label.pack(pady=(0, 15))

        # Выбор счёта
        account_label = tb.Label(left_menu, text="Счёт:",
                                  bootstyle="inverse-dark",
                                  font=('Arial', 12, 'bold'))
        account_label.pack(anchor=tk.W, pady=(5, 2))

        self.account_combo_var = tk.StringVar(value="Все")
        self.account_combo = tb.Combobox(left_menu, textvariable=self.account_combo_var,
                                          state="readonly", width=25,
                                          bootstyle="dark")
        self.account_combo.pack(fill=tk.X, pady=(0, 10))
        self.account_combo.bind('<<ComboboxSelected>>', self._on_account_change)
        self._update_account_combo()

        # Кнопки меню
        self.menu_buttons = []
        menu_items = [
            ("Депозитные счета", self.show_accounts),
            ("Активы", self.show_assets),
            ("Все транзакции", self.show_transactions),
            ("Срезы", self.show_cuts),
            ("Анализ", self.show_analysis),
            ("Настройки", self.show_settings),
        ]

        for text, command in menu_items:
            btn = tb.Button(left_menu, text=text, command=command, bootstyle="secondary")
            btn.pack(fill=tk.X, pady=5)
            self.menu_buttons.append(btn)

        # Правая часть (контент) — светлый фон от темы cosmo
        self.content_frame = tb.Frame(main_frame)
        self.content_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Показываем счета по умолчанию
        self.show_accounts()

    def _update_account_combo(self):
        """Обновить список счетов в combobox."""
        accounts = get_all_accounts()
        values = ["Все"]
        for a in accounts:
            acct_num = a["account_number"] if "account_number" in a.keys() else ""
            if acct_num:
                values.append(f'{a["name"]} · {acct_num}')
            else:
                values.append(a["name"])
        self.account_combo['values'] = values

    def _on_account_change(self, event=None):
        """Обработчик смены счёта."""
        selected = self.account_combo_var.get()
        if selected == "Все":
            self.selected_account_id.set(0)
        else:
            accounts = get_all_accounts()
            for a in accounts:
                acct_num = a["account_number"] if "account_number" in a.keys() else ""
                if acct_num:
                    display = f'{a["name"]} · {acct_num}'
                else:
                    display = a["name"]
                if display == selected:
                    self.selected_account_id.set(a["id"])
                    break

        # Обновляем активы/транзакции если на соответствующих вкладках
        if hasattr(self, 'assets_view'):
            account_id = self.selected_account_id.get()
            account_id = account_id if account_id > 0 else None
            self.assets_view.set_broker(account_id)
            self.assets_view.refresh()

        if hasattr(self, 'transactions_view'):
            self.transactions_view.refresh()

        if hasattr(self, 'cuts_view'):
            self.cuts_view.refresh()

        if hasattr(self, 'analysis_view'):
            self.analysis_view.refresh()

    def clear_content(self):
        """Очистка правой части."""
        for widget in self.content_frame.winfo_children():
            widget.destroy()

    def show_accounts(self):
        """Показать вкладку депозитных счетов."""
        self.clear_content()
        self.accounts_view = AccountsView(self.content_frame, controller=self)
        self.accounts_view.pack(fill=tk.BOTH, expand=True)
        self.update_menu_highlight(0)

    def show_assets(self):
        """Показать вкладку активов."""
        self.clear_content()
        account_id = self.selected_account_id.get()
        account_id = account_id if account_id > 0 else None
        self.assets_view = AssetsView(self.content_frame, controller=self)
        self.assets_view.pack(fill=tk.BOTH, expand=True)
        self.assets_view.set_broker(account_id)
        self.assets_view.refresh()
        self.update_menu_highlight(1)

    def show_transactions(self):
        """Показать вкладку всех транзакций."""
        self.clear_content()
        self.transactions_view = TransactionsView(self.content_frame, controller=self)
        self.transactions_view.pack(fill=tk.BOTH, expand=True)
        self.update_menu_highlight(2)

    def show_cuts(self):
        """Показать вкладку срезов."""
        self.clear_content()
        self.cuts_view = CutsView(self.content_frame, controller=self)
        self.cuts_view.pack(fill=tk.BOTH, expand=True)
        self.update_menu_highlight(3)

    def show_analysis(self):
        """Показать вкладку анализа."""
        self.clear_content()
        self.analysis_view = AnalysisView(self.content_frame, controller=self)
        self.analysis_view.pack(fill=tk.BOTH, expand=True)
        self.update_menu_highlight(4)

    def show_settings(self):
        """Показать вкладку настроек."""
        self.clear_content()
        self.settings_view = SettingsView(self.content_frame, controller=self)
        self.settings_view.pack(fill=tk.BOTH, expand=True)
        self.update_menu_highlight(5)

    def update_menu_highlight(self, active_index):
        """Подсветка активной кнопки меню."""
        for i, btn in enumerate(self.menu_buttons):
            if i == active_index:
                btn.config(bootstyle="primary")
            else:
                btn.config(bootstyle="secondary")

    def relaunch_app(self):
        """Перезапустить приложение."""
        if getattr(sys, 'frozen', False):
            subprocess.Popen([sys.executable])
        else:
            subprocess.Popen([sys.executable, os.path.abspath(__file__)])
        self.root.destroy()


def main():
    root = tb.Window(themename="cosmo")
    app = InvestmentApp(root)
    root.protocol("WM_DELETE_WINDOW", lambda: on_close(root))
    root.mainloop()


if __name__ == "__main__":
    bootstrap_db()
    init_schema()
    main()

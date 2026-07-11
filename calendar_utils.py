"""Общие помощники для работы с календарём (DateEntry с русской локалью)."""

import tkinter as tk
from datetime import date
from tkcalendar import DateEntry
from babel.dates import get_month_names

# --- Cosmo-палитра для выпадающего календаря ---
_CAL_COLORS = {
    # Выбранная дата
    'selectbackground': '#2780e3',
    'selectforeground': 'white',
    # Обычные будни
    'normalbackground': 'white',
    'normalforeground': '#333333',
    # Выходные
    'weekendbackground': '#fafafa',
    'weekendforeground': '#c0392b',
    # Дни другого месяца
    'othermonthforeground': '#cfd8dc',
    'othermonthbackground': '#fafafa',
    'othermonthweforeground': '#f5b7b1',
    'othermonthwebackground': '#fafafa',
    # Шапка дней недели
    'headersbackground': 'white',
    'headersforeground': '#2780e3',
    # Общие
    'background': 'white',
    'foreground': '#333333',
    'bordercolor': '#dee2e6',
}


def create_date_entry(parent, initial_date=None, width=28, **kw):
    """Создать DateEntry с русской локалью, cosmo-цветами и фиксом курсора.

    Параметры:
        parent: родительский виджет.
        initial_date: datetime.date / datetime.datetime — начальная дата.
        width: ширина поля ввода (по умолчанию 28).
        **kw: дополнительные параметры для DateEntry / Calendar.

    Возвращает:
        tkcalendar.DateEntry.
    """
    # русская локаль + фикс курсора
    kw.setdefault('locale', 'ru_RU')
    kw.setdefault('date_pattern', 'dd.mm.yyyy')
    kw.setdefault('cursor', 'arrow')

    date_entry = DateEntry(parent, width=width, **kw)

    # Фикс: _on_motion в tkcalendar по умолчанию ставит I-beam (xterm),
    # который на Windows под ttkbootstrap исчезает. Сохраняем 'arrow'.
    date_entry._cursor = 'arrow'

    # Фикс: ttkbootstrap навешивает on_disabled_readonly_state на <Configure>
    # для всех TEntry-виджетов. Для normal-state полей она делает
    # widget['cursor'] = None, что приводит к _cursor='None' (строка) и
    # исчезновению курсора на Windows. Перехватываем cursor=None в configure.
    _orig_configure = date_entry.configure
    def _safe_configure(cnf={}, **ckw):
        if not isinstance(cnf, dict):
            return _orig_configure(cnf, **ckw)
        merged = dict(cnf)
        merged.update(ckw)
        if merged.get('cursor') is None:
            merged['cursor'] = 'arrow'
        return _orig_configure(**merged)
    date_entry.configure = _safe_configure
    date_entry.config = _safe_configure

    # Красивые цвета выпадающего календаря
    colors = dict(_CAL_COLORS)
    if 'colors' in kw:
        colors.update(kw.pop('colors'))
    try:
        date_entry._calendar.configure(**colors)
    except Exception:
        # Если какие-то цвета не поддерживаются в этой версии tkcalendar —
        # пропускаем silently, календарь всё равно работает.
        pass

    # Начальная дата
    if initial_date is not None:
        date_entry.set_date(initial_date)

    # Фикс: оригинальный _on_b1_press открывает календарь только при клике
    # на элемент 'downarrow' (~15px). Клик по видимой стрелке часто не
    # попадает в эту область. Делаем кликабельным всё поле — drop_down()
    # работает как toggle (открыт → закроет, закрыт → откроет).
    def _on_b1_press_fixed(event):
        if 'disabled' not in date_entry.state():
            date_entry.drop_down()
    date_entry.bind('<ButtonPress-1>', _on_b1_press_fixed)

    # Фикс: оригинальный _on_focus_out_cal закрывает календарь, когда фокус
    # уходит на любой виджет кроме entry. Кнопки навигации по месяцам/годам
    # внутри popup забирают фокус → календарь закрывается. Если новый фокус
    # внутри _top_cal — оставляем календарь открытым.
    def _on_focus_out_cal_fixed(event):
        focus = date_entry.focus_get()
        if focus is not None:
            if focus == date_entry:
                x, y = event.x, event.y
                if (type(x) != int or type(y) != int or
                        date_entry.identify(x, y) != date_entry._downarrow_name):
                    date_entry._top_cal.withdraw()
                    date_entry.state(['!pressed'])
            elif focus.winfo_toplevel() == date_entry._top_cal:
                pass
            else:
                date_entry._top_cal.withdraw()
                date_entry.state(['!pressed'])
        elif date_entry.grab_current():
            x, y = date_entry._top_cal.winfo_pointerxy()
            xc = date_entry._top_cal.winfo_rootx()
            yc = date_entry._top_cal.winfo_rooty()
            w = date_entry._top_cal.winfo_width()
            h = date_entry._top_cal.winfo_height()
            if xc <= x <= xc + w and yc <= y <= yc + h:
                date_entry._calendar.focus_force()
            else:
                date_entry._top_cal.withdraw()
                date_entry.state(['!pressed'])
        else:
            if 'active' in date_entry.state():
                date_entry._calendar.focus_force()
            else:
                date_entry._top_cal.withdraw()
                date_entry.state(['!pressed'])
    date_entry._calendar.bind('<FocusOut>', _on_focus_out_cal_fixed)

    # Фикс: Babel для context='format' (по умолчанию) возвращает месяцы в
    # родительном падеже (июня, июля). Для заголовка календаря нужен
    # именительный (июнь, июль) — context='stand-alone'.
    date_entry._calendar._month_names = get_month_names(
        'wide', context='stand-alone', locale='ru_RU')
    date_entry._calendar._display_calendar()

    return date_entry

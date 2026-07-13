"""Расчёт показателей доходности портфеля: TWR и XIRR.

Чистый Python, без сторонних зависимостей (scipy/numpy не требуются).
Используются месячные срезы (snapshots), агрегированные по счетам.

Условные обозначения:
  BMV  (Begin Market Value) — стоимость портфеля на начало месяца;
  EMV  (End Market Value)   — стоимость портфеля на конец месяца;
  CF   (Cash Flow)          — чистый внешний приток капитала за месяц
                              (пополнения минус списания).

Дивиденды и купоны НЕ являются внешними потоками — это доход портфеля,
поэтому в расчёте TWR/XIRR не вычитаются.
"""

import calendar
from datetime import datetime

# Порог ниже которого считаем, что значение равно нулю
_EPS = 1e-9


# ================================================================
#  Вспомогательные функции дат
# ================================================================

def month_end_date(ym):
    """Вернуть datetime последнего дня месяца из строки 'YYYY-MM'."""
    y, m = ym.split('-')
    y, m = int(y), int(m)
    last_day = calendar.monthrange(y, m)[1]
    return datetime(y, m, last_day)


def prev_month_ym(ym):
    """Вернуть 'YYYY-MM' предыдущего месяца."""
    y, m = ym.split('-')
    y, m = int(y), int(m)
    m -= 1
    if m < 1:
        m, y = 12, y - 1
    return f"{y:04d}-{m:02d}"


# ================================================================
#  XIRR — внутренняя норма доходности (годовых)
# ================================================================

def xnpv(rate, cashflows):
    """Чистая приведённая стоимость потоков по годовой ставке rate.

    cashflows: список [(datetime, amount), ...]; отсчёт времени — от
    самой ранней даты. Возвращает сумму дисконтированных потоков.
    """
    if not cashflows:
        return 0.0
    t0 = cashflows[0][0]
    total = 0.0
    for date, amount in cashflows:
        years = (date - t0).days / 365.0
        total += amount / ((1.0 + rate) ** years)
    return total


def _xirr_newton(cashflows, guess=0.1):
    """Резервный метод: Ньютона с численной производной."""
    rate = guess
    eps = 1e-7
    for _ in range(100):
        f = xnpv(rate, cashflows)
        df = (xnpv(rate + eps, cashflows) - xnpv(rate - eps, cashflows)) / (2 * eps)
        if abs(df) < 1e-12:
            return None
        new_rate = rate - f / df
        # ограничиваем ставку разумным диапазоном
        if new_rate <= -0.9999:
            new_rate = -0.99
        if abs(new_rate - rate) < 1e-9:
            return new_rate
        rate = new_rate
    return rate


def xirr(cashflows):
    """Внутренняя норма доходности для нерегулярных потоков.

    cashflows: список [(datetime, amount), ...].
    Возвращает ставку в долях годовых (например, 0.164 = 16.4 %),
    либо None, если решение не существует или не сходится.
    """
    if not cashflows or len(cashflows) < 2:
        return None

    cashflows = sorted(cashflows, key=lambda x: x[0])

    # Должны быть потоки обоих знаков, иначе IRR не определён
    has_pos = any(a > _EPS for _, a in cashflows)
    has_neg = any(a < -_EPS for _, a in cashflows)
    if not has_pos or not has_neg:
        return None

    # Метод бисекции на надёжном диапазоне ставок
    lo, hi = -0.999, 10.0
    f_lo = xnpv(lo, cashflows)
    f_hi = xnpv(hi, cashflows)

    if abs(f_lo) < _EPS:
        return lo
    if abs(f_hi) < _EPS:
        return hi
    if f_lo * f_hi > 0:
        # корня нет в диапазоне — пробуем Ньютон
        return _xirr_newton(cashflows)

    for _ in range(200):
        mid = (lo + hi) / 2.0
        f_mid = xnpv(mid, cashflows)
        if abs(f_mid) < 1e-7:
            return mid
        if f_lo * f_mid < 0:
            hi = mid
            f_hi = f_mid
        else:
            lo = mid
            f_lo = f_mid
    return (lo + hi) / 2.0


def build_xirr_cashflows(monthly_rows, start_value, start_ym):
    """Построить денежные потоки для XIRR с точки зрения инвестора.

    monthly_rows : список строк срезов по возрастанию даты
                   (поля: ym, portfolio_total_rub, deposits, withdrawals);
    start_value  : стоимость портфеля на начало периода (BMV) или None;
    start_ym     : 'YYYY-MM' точки отсчёта (месяц, предшествующий периоду).

    Конвенция потоков: пополнения — отток (минус, деньги вложены),
    списания и конечная стоимость — приток (плюс, деньги вернулись).
    """
    cashflows = []

    # Начальный капитал (существующий портфель) — отток на старте периода
    if start_value and start_value > _EPS:
        cashflows.append((month_end_date(start_ym), -start_value))

    # Ежемесячные внешние потоки
    for r in monthly_rows:
        dep = r["deposits"] or 0
        wdr = r["withdrawals"] or 0
        net = wdr - dep  # приток инвестору — положительный
        if abs(net) > _EPS:
            cashflows.append((month_end_date(r["ym"]), net))

    # Конечная стоимость портфеля — приток на конец периода
    if monthly_rows:
        end_value = monthly_rows[-1]["portfolio_total_rub"] or 0
        if end_value > _EPS:
            cashflows.append((month_end_date(monthly_rows[-1]["ym"]), end_value))

    cashflows.sort(key=lambda x: x[0])
    return cashflows


# ================================================================
#  TWR — взвешенная по времени доходность
# ================================================================

def twr_series(monthly_rows, start_value):
    """Накопительная взвешенная по времени доходность по месяцам.

    monthly_rows : список строк срезов по возрастанию даты
                   (поля: ym, portfolio_total_rub, deposits, withdrawals);
    start_value  : стоимость портфеля на начало первого месяца (BMV) или None.

    Возвращает кортеж:
      (список [(ym, cumulative_pct), ...], итоговая_доля).
    Под-периодный доход считается в предположении, что чистый поток
    поступает в начале месяца:
        r_i = EMV / (BMV + CF) - 1,   CF = deposits - withdrawals
    Накопительный TWR = (1+r_1)(1+r_2)...(1+r_n) - 1.

    Месяцы без данных (стоимость портфеля <= 0 — отсутствующий срез)
    пропускаются: их денежные потоки переносятся на следующий
    валидный месяц, чтобы разрыв не искажал связанную доходность.
    """
    cum_factor = 1.0
    series = []
    bmv = start_value if start_value and start_value > _EPS else 0.0
    pending_cf = 0.0  # потоки за пропущенные месяцы

    for r in monthly_rows:
        emv = r["portfolio_total_rub"] or 0
        dep = r["deposits"] or 0
        wdr = r["withdrawals"] or 0
        cf = dep - wdr  # чистый приток капитала

        # Пропуск месяцев без данных (нет среза портфеля)
        if emv <= _EPS:
            pending_cf += cf
            continue

        base = bmv + cf + pending_cf
        if abs(base) > _EPS:
            r_i = emv / base - 1.0
        else:
            r_i = 0.0

        cum_factor *= (1.0 + r_i)
        series.append((r["ym"], (cum_factor - 1.0) * 100.0))
        bmv = emv  # конец месяца становится началом следующего
        pending_cf = 0.0

    return series, (cum_factor - 1.0)


def twr_annualized(twr_fraction, monthly_rows):
    """Годовая (аннуализированная) ставка TWR из накопительной доли.

    twr_fraction — накопительная доля (0.428 = 42.8 %) за весь период;
    monthly_rows — список срезов (для расчёта длительности периода в годах).
    Период отсчитывается от конца месяца, предшествующего первому срезу
    (точки старта начального капитала), до конца последнего среза.
    Возвращает долю годовых или None при недостатке данных.
    """
    if not monthly_rows or abs(twr_fraction + 1.0) < _EPS:
        return None
    start = month_end_date(prev_month_ym(monthly_rows[0]["ym"]))
    last = month_end_date(monthly_rows[-1]["ym"])
    years = (last - start).days / 365.0
    if years < _EPS:
        return None
    if twr_fraction <= -1.0:
        return None
    return (1.0 + twr_fraction) ** (1.0 / years) - 1.0

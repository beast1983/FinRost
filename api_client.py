"""
Клиент для получения текущих цен через API Московской биржи.
"""

import requests
from requests.exceptions import ConnectionError, Timeout


API_BASE = "https://iss.moex.com/iss"
TIMEOUT = 5  # таймаут запроса в секундах


def _fetch_iss_data(url):
    """Выполнить GET запрос к ISS API и вернуть JSON."""
    try:
        response = requests.get(url, timeout=TIMEOUT)
        if response.status_code == 200:
            return response.json()
        else:
            return None
    except ConnectionError:
        return None
    except Timeout:
        return None
    except Exception:
        return None


def fetch_price(ticker, asset_type):
    """
    Запрос текущей цены через API Московской биржи.

    Args:
        ticker (str): Тикер акции или облигации (например, 'SBER', 'GAZP')
        asset_type (str): 'акция', 'облигация' или 'etf'

    Returns:
        tuple: (price, None) при успехе или (None, error_message) при ошибке.
               price — float цена одной бумаги, либо None.
    """
    ticker = ticker.upper().strip()

    if asset_type == "акция":
        price = _fetch_share_price(ticker)
    elif asset_type == "облигация":
        price = _fetch_bond_price(ticker)
    elif asset_type == "etf":
        price = _fetch_share_price(ticker)
    else:
        return None, f"Неизвестный тип актива: {asset_type}"

    if price is not None and price > 0:
        return round(price, 2), None
    else:
        return None, f"Цена для {ticker} не найдена на бирже"


def _fetch_price_generic(ticker, market):
    """
    Универсальное получение цены бумаги (акция/ETF/облигация) без привязки к доске.

    Алгоритм:
    1. Запрос без доски — https://iss.moex.com/iss/engines/stock/markets/{market}/securities/{ticker}.json
    2. marketdata.LAST (первая ненулевая по любой доске — текущая цена сделки)
    3. securities.PREVPRICE (цена закрытия прошлой сессии — приоритетный fallback)
    4. securities.PREVWAPRICE → PREVLEGALCLOSEPRICE → marketdata.MARKETPRICE → MARKETPRICE2 → LCURRENTPRICE

    Args:
        ticker (str): Тикер бумаги
        market (str): 'shares' или 'bonds'

    Returns:
        float или None — цена бумаги, либо None при ошибке/отсутствии данных.
    """
    url = f"{API_BASE}/engines/stock/markets/{market}/securities/{ticker}.json"
    data = _fetch_iss_data(url)
    if data is None:
        return None

    try:
        marketdata = data.get("marketdata", {})
        md_cols = marketdata.get("columns", [])
        md_rows = marketdata.get("data", [])
        securities = data.get("securities", {})
        sec_cols = securities.get("columns", [])
        sec_rows = securities.get("data", [])

        md_idx = {name: i for i, name in enumerate(md_cols)}
        sec_idx = {name: i for i, name in enumerate(sec_cols)}

        def first_nonnull(rows, idx_map, col):
            """Найти первое непустое положительное числовое значение по имени колонки."""
            i = idx_map.get(col)
            if i is None:
                return None
            for row in rows:
                if len(row) > i and row[i] is not None:
                    val = row[i]
                    try:
                        val = float(val)
                    except (TypeError, ValueError):
                        continue
                    if val > 0:
                        return val
            return None

        # 1. LAST (по любой доске) — текущая цена последней сделки
        price = first_nonnull(md_rows, md_idx, "LAST")
        if price:
            return price

        # 2. PREVPRICE из securities — приоритетный fallback (цена закрытия прошлой сессии)
        price = first_nonnull(sec_rows, sec_idx, "PREVPRICE")
        if price:
            return price

        # 3. Дополнительные fallback'ы из securities
        for col in ("PREVWAPRICE", "PREVLEGALCLOSEPRICE"):
            price = first_nonnull(sec_rows, sec_idx, col)
            if price:
                return price

        # 4. Дополнительные fallback'ы из marketdata
        for col in ("MARKETPRICE", "MARKETPRICE2", "LCURRENTPRICE"):
            price = first_nonnull(md_rows, md_idx, col)
            if price:
                return price
    except (ValueError, TypeError, IndexError, KeyError):
        return None

    return None


def _fetch_share_price(ticker):
    """Получить цену акции/ETF через движок stock, рынок shares (все доски)."""
    return _fetch_price_generic(ticker, "shares")


def _fetch_bond_price(ticker):
    """Получить цену облигации через движок stock, рынок bonds (все доски)."""
    return _fetch_price_generic(ticker, "bonds")


def _fetch_bond_static(ticker):
    """
    Получить статические данные облигации из блока securities.
    
    Возвращает dict с ключами:
        face_value (float) — номинал облигации
        lot_size (int)     — количество бумаг в лоте
        list_level (int)   — уровень листинга (1, 2, 3)
    
    Возвращает None при ошибке.
    """
    url = f"{API_BASE}/engines/stock/markets/bonds/securities/{ticker}.json"
    
    try:
        data = _fetch_iss_data(url)
        if data is None:
            return None
        
        securities = data.get("securities", {})
        columns = securities.get("columns", [])
        rows = securities.get("data", [])
        
        if not rows or not columns:
            return None
        
        col_map = {name: idx for idx, name in enumerate(columns)}
        
        face_value = None
        lot_size = None
        list_level = None
        coupon_percent = None
        
        row = rows[0]
        face_idx = col_map.get("FACEVALUE")
        lot_idx = col_map.get("LOTSIZE")
        level_idx = col_map.get("LISTLEVEL")
        coupon_idx = col_map.get("COUPONPERCENT")
        
        if face_idx is not None and len(row) > face_idx and row[face_idx] is not None:
            face_value = float(row[face_idx])
        if lot_idx is not None and len(row) > lot_idx and row[lot_idx] is not None:
            lot_size = int(row[lot_idx])
        if level_idx is not None and len(row) > level_idx and row[level_idx] is not None:
            list_level = int(row[level_idx])
        if coupon_idx is not None and len(row) > coupon_idx and row[coupon_idx] is not None:
            coupon_percent = float(row[coupon_idx])
        
        return {
            "face_value": face_value,
            "lot_size": lot_size,
            "list_level": list_level,
            "coupon_percent": coupon_percent,
        }
        
    except (ValueError, TypeError, IndexError, KeyError):
        return None
    except Exception:
        return None


def is_connected():
    """Проверить наличие подключения к интернету (пинг ISS API)."""
    try:
        r = requests.get("https://iss.moex.com", timeout=TIMEOUT)
        return r.status_code == 200
    except Exception:
        return False


# URL API ЦБ РФ
CBR_API_URL = "https://www.cbr-xml-daily.ru/daily_json.js"


def fetch_cbr_exchange_rates():
    """
    Получить курсы валют ЦБ РФ.

    Returns:
        dict или None: Словарь с данными валют при успехе.
                       Ключи: 'USD.Value', 'EUR.Value' — курсы относительно рубля.
                       Пример: {'USD': 89.5, 'EUR': 97.3}
                       None при ошибке или отсутствии интернета.
    """
    try:
        response = requests.get(CBR_API_URL, timeout=TIMEOUT)
        if response.status_code != 200:
            return None

        data = response.json()

        usd_value = None
        eur_value = None
        cny_value = None

        valute = data.get("Valute", {})

        if "USD" in valute:
            usd_value = valute["USD"].get("Value")
        if "EUR" in valute:
            eur_value = valute["EUR"].get("Value")
        if "CNY" in valute:
            cny_value = valute["CNY"].get("Value")

        result = {}
        if usd_value is not None and usd_value > 0:
            result["USD"] = round(usd_value, 4)
        if eur_value is not None and eur_value > 0:
            result["EUR"] = round(eur_value, 4)
        if cny_value is not None and cny_value > 0:
            result["CNY"] = round(cny_value, 4)

        return result if result else None

    except ConnectionError:
        return None
    except Timeout:
        return None
    except Exception:
        return None


def fetch_cbr_usd_rate():
    """
    Получить курс USD к RUB из ЦБ РФ.

    Returns:
        float или None — курс USD, или None при ошибке.
    """
    rates = fetch_cbr_exchange_rates()
    if rates and "USD" in rates:
        return rates["USD"]
    return None


def fetch_cbr_eur_rate():
    """
    Получить курс EUR к RUB из ЦБ РФ.

    Returns:
        float или None — курс EUR, или None при ошибке.
    """
    rates = fetch_cbr_exchange_rates()
    if rates and "EUR" in rates:
        return rates["EUR"]
    return None

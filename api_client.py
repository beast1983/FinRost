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


def _fetch_price_from_marketdata(ticker, engine, market, board):
    """
    Получить цену ценной бумаги через API Московской биржи.

    Args:
        ticker (str): Тикер бумаги (например, 'GAZP')
        engine (str): Движок — 'stock'
        market (str): Рынок — 'shares' или 'bonds'
        board (str): Торгующая система — 'TQBR' для акций, 'TQOB' для облигаций

    Returns:
        float или None — последняя цена, либо None при ошибке/отсутствии данных.
    """
    url = (
        f"{API_BASE}/engines/{engine}/markets/{market}/boards/{board}/securities/{ticker}.json"
    )
    data = _fetch_iss_data(url)
    if data is None:
        return None

    # Парсим ответ: данные находятся в сегменте "marketdata" -> "data"
    # Структура:
    #   "marketdata": {
    #       "columns": ["SECID", "BOARDID", "LAST", ...],
    #       "data": [["GAZP", "TQBR", 150.5, ...]]
    #   }
    try:
        marketdata = data.get("marketdata", {})
        col_names = marketdata.get("columns", [])
        rows = marketdata.get("data", [])

        # Находим индекс колонки "LAST"
        last_idx = None
        for i, name in enumerate(col_names):
            if name == "LAST":
                last_idx = i
                break

        if last_idx is None:
            return None

        # Проходим по строкам данных ищем нужный тикер
        for row in rows:
            if len(row) > last_idx and row[0] == ticker:
                last_price = row[last_idx]
                if last_price is not None:
                    return float(last_price)
    except (ValueError, TypeError, IndexError, KeyError):
        return None

    return None


def _fetch_share_price(ticker):
    """Получить цену акции через движок stock, рынок shares, доска TQBR."""
    return _fetch_price_from_marketdata(ticker, "stock", "shares", "TQBR")


def _fetch_bond_price(ticker):
    """
    Универсальная функция для облигаций — без привязки к доске.
    
    Алгоритм:
    1. Запрашиваем https://iss.moex.com/iss/engines/stock/markets/bonds/securities/{ticker}.json
       БЕЗ указания доски — API вернёт данные по всем доскам
    2. Берём первую строку с ненулевой ценой LAST
    """
    url = f"{API_BASE}/engines/stock/markets/bonds/securities/{ticker}.json"
    
    try:
        data = _fetch_iss_data(url)
        if data is None:
            return None
        
        marketdata = data.get("marketdata", {})
        columns = marketdata.get("columns", [])
        rows = marketdata.get("data", [])
        
        if not rows or not columns:
            return None
        
        # Находим индекс колонки "LAST"
        last_idx = None
        for i, name in enumerate(columns):
            if name == "LAST":
                last_idx = i
                break
        
        if last_idx is None:
            return None
        
        # Ищем первую строку с ненулевой ценой
        for row in rows:
            if len(row) > last_idx and row[last_idx] is not None:
                price = float(row[last_idx])
                if price > 0:
                    return price
                    
    except (ValueError, TypeError, IndexError, KeyError):
        return None
    except Exception:
        return None
    
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

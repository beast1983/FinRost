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


def _get_securities_data(ticker, market):
    """Получить данные securities из указанного рынка.

    Returns:
        dict с ключами columns и data, либо None при ошибке.
    """
    url = f"{API_BASE}/engines/stock/markets/{market}/securities/{ticker}.json"
    return _fetch_iss_data(url)


def fetch_ticker_static(ticker):
    """Получить статические данные тикера из Мосбиржи.

    Возвращает:
        dict с ключами:
            shortname (str) — короткое название бумаги
            currency (str)  — валюта (SUR маппится в RUB)
            lot_size (int)  — количество бумаг в лоте
            asset_type (str) — 'акция' / 'облигация' / 'etf'
        None при ошибке/отсутствии данных.
    """
    ticker = ticker.upper().strip()
    if not ticker:
        return None

    CURRENCY_MAP = {'SUR': 'RUB', 'RUR': 'RUB'}

    def _parse_static(securities_block, market):
        """Распарсить securities блок и вернуть словарь данных.

        Для акций (market == "shares") — выбирает строку канонической доски
        (TQBR для акций, TQTF для ETF) с fallback на первую строку.
        Для облигаций (market == "bonds") — валюту берёт из FACEUNIT,
        а lot_size — из FACEVALUE (номинал), с fallback на LOTSIZE.
        """
        if not securities_block:
            return None
        columns = securities_block.get("columns", [])
        rows = securities_block.get("data", [])
        if not rows or not columns:
            return None
        col_map = {name: idx for idx, name in enumerate(columns)}

        board_idx = col_map.get("BOARDID")
        st_idx = col_map.get("SECTYPE")

        # Выбор строки по доске для акций (TQBR для акций, TQTF для ETF)
        row = rows[0]
        if market == "shares":
            etf_board_ids = {'TQTF', 'TQTD', 'TQIF', 'EQRP'}
            etf_sectypes = {'J', 'M', 'I'}
            # Определяем, ETF ли это (по любой строке)
            is_etf = False
            for r in rows:
                bid = r[board_idx] if (board_idx is not None and len(r) > board_idx and r[board_idx] is not None) else None
                sec = r[st_idx] if (st_idx is not None and len(r) > st_idx and r[st_idx] is not None) else None
                if bid in etf_board_ids or (sec and str(sec) in etf_sectypes):
                    is_etf = True
                    break
            preferred_board = "TQTF" if is_etf else "TQBR"
            for r in rows:
                if len(r) > board_idx and r[board_idx] == preferred_board:
                    row = r
                    break

        shortname = ''
        sn_idx = col_map.get("SHORTNAME")
        if sn_idx is not None and len(row) > sn_idx and row[sn_idx] is not None:
            shortname = str(row[sn_idx]).strip()

        currency = ''
        if market == "bonds":
            # Для облигаций валюта — FACEUNIT (номинал), а не CURRENCYID доски
            cur_idx = col_map.get("FACEUNIT")
            if cur_idx is not None and len(row) > cur_idx and row[cur_idx] is not None:
                cur_code = str(row[cur_idx]).strip()
                currency = CURRENCY_MAP.get(cur_code, cur_code)
        else:
            cur_idx = col_map.get("CURRENCYID")
            if cur_idx is not None and len(row) > cur_idx and row[cur_idx] is not None:
                cur_code = str(row[cur_idx]).strip()
                currency = CURRENCY_MAP.get(cur_code, cur_code)

        lot_size = 1
        if market == "bonds":
            # Для облигаций — FACEVALUE (номинал), с fallback на LOTSIZE
            fv_idx = col_map.get("FACEVALUE")
            if fv_idx is not None and len(row) > fv_idx and row[fv_idx] is not None:
                try:
                    lot_size = int(float(row[fv_idx]))
                except (ValueError, TypeError):
                    lot_size = 1
            if lot_size <= 0:
                lot_idx = col_map.get("LOTSIZE")
                if lot_idx is not None and len(row) > lot_idx and row[lot_idx] is not None:
                    try:
                        lot_size = int(row[lot_idx])
                    except (ValueError, TypeError):
                        lot_size = 1
        else:
            lot_idx = col_map.get("LOTSIZE")
            if lot_idx is not None and len(row) > lot_idx and row[lot_idx] is not None:
                try:
                    lot_size = int(row[lot_idx])
                except (ValueError, TypeError):
                    lot_size = 1

        asset_type = ''
        sectype = None
        if st_idx is not None and len(row) > st_idx and row[st_idx] is not None:
            try:
                sectype = str(row[st_idx])
            except (ValueError, TypeError):
                sectype = None
        board_id = None
        if board_idx is not None and len(row) > board_idx and row[board_idx] is not None:
            board_id = str(row[board_idx])

        # Определение типа актива
        if market == "bonds":
            asset_type = "облигация"
        else:
            # Рынок shares
            etf_board_ids = {'TQTF', 'TQTD', 'TQIF', 'EQRP'}
            etf_sectypes = {'J', 'M', 'I'}
            if board_id in etf_board_ids or (sectype and sectype in etf_sectypes):
                asset_type = "etf"
            else:
                asset_type = "акция"

        return {
            "shortname": shortname,
            "currency": currency,
            "lot_size": lot_size,
            "asset_type": asset_type,
        }

    # Шаг 1: пробуем рынок shares
    data = _get_securities_data(ticker, "shares")
    if data and data.get("securities", {}).get("data"):
        result = _parse_static(data.get("securities"), "shares")
        if result and result["shortname"]:
            return result

    # Шаг 2: пробуем рынок bonds
    data = _get_securities_data(ticker, "bonds")
    if data and data.get("securities", {}).get("data"):
        result = _parse_static(data.get("securities"), "bonds")
        if result and result["shortname"]:
            return result

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


def _fetch_share_static(ticker, preferred_board="TQBR"):
    """
    Получить статические данные акции/ETF из блока securities.

    Запрос без доски возвращает несколько строк (по одной на каждую доску:
    SMAL, SPEQ, TQBR и т.д.), причём LOTSIZE различается между ними
    (например у GAZP на SMAL = 1, на TQBR = 10). Поэтому выбираем строку
    канонической доски (TQBR для акций, TQTF для ETF), с fallback на первую.

    Args:
        ticker (str): Тикер бумаги
        preferred_board (str): Предпочитаемая доска ('TQBR' для акций, 'TQTF' для ETF)

    Returns:
        dict с ключами:
            lot_size (int)   — количество бумаг в лоте
            list_level (int) — уровень листинга (1, 2, 3)
        None при ошибке.
    """
    url = f"{API_BASE}/engines/stock/markets/shares/securities/{ticker}.json"

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

        # Поиск строки канонической доски, fallback на первую строку
        board_idx = col_map.get("BOARDID")
        row = rows[0]
        if board_idx is not None:
            for r in rows:
                if len(r) > board_idx and r[board_idx] == preferred_board:
                    row = r
                    break

        lot_size = None
        list_level = None

        lot_idx = col_map.get("LOTSIZE")
        level_idx = col_map.get("LISTLEVEL")

        if lot_idx is not None and len(row) > lot_idx and row[lot_idx] is not None:
            lot_size = int(row[lot_idx])
        if level_idx is not None and len(row) > level_idx and row[level_idx] is not None:
            list_level = int(row[level_idx])

        return {
            "lot_size": lot_size,
            "list_level": list_level,
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

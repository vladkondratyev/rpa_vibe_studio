"""Банковские утилиты: контрольные суммы реквизитов, сумма прописью,
формат обмена «1С:Предприятие ↔ Клиент банка» (1CClientBankExchange),
курсы и ключевая ставка Банка России, сверка с перечнями (115-ФЗ)."""
from __future__ import annotations

import datetime as dt
import difflib
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple


# ---------------------------------------------------------------- контрольные суммы
def _digits(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


def check_inn(value: Any) -> bool:
    s = _digits(value)
    d = [int(c) for c in s]

    def ctrl(weights: Iterable[int]) -> int:
        return sum(w * x for w, x in zip(weights, d)) % 11 % 10

    if len(s) == 10:
        return ctrl([2, 4, 10, 3, 5, 9, 4, 6, 8]) == d[9]
    if len(s) == 12:
        return (ctrl([7, 2, 4, 10, 3, 5, 9, 4, 6, 8]) == d[10]
                and ctrl([3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8]) == d[11])
    return False


def check_snils(value: Any) -> bool:
    s = _digits(value)
    if len(s) != 11:
        return False
    if int(s[:9]) <= 1001998:
        return True  # для старых номеров контрольное число не проверяется
    total = sum(int(c) * (9 - i) for i, c in enumerate(s[:9]))
    if total > 101:
        total %= 101
    ctrl = 0 if total in (100, 101) else total
    return ctrl == int(s[9:])


def check_ogrn(value: Any) -> bool:
    s = _digits(value)
    if len(s) == 13:
        return int(s[:12]) % 11 % 10 == int(s[12])
    if len(s) == 15:  # ОГРНИП
        return int(s[:14]) % 13 % 10 == int(s[14])
    return False


def check_account(account: Any, bik: Any) -> bool:
    """Контрольный ключ 20-значного счёта по БИК (положение Банка России № 579-П)."""
    acc, b = _digits(account), _digits(bik)
    if len(acc) != 20 or len(b) != 9:
        return False
    # корсчёт в Банке России — «0» + 5-я и 6-я цифры БИК; счёт в кредитной организации — 3 последние цифры БИК
    prefix = "0" + b[4:6] if acc.startswith("301") else b[6:9]
    weights = [7, 1, 3] * 8
    return sum(int(c) * w for c, w in zip(prefix + acc, weights)) % 10 == 0


# ---------------------------------------------------------------- сумма прописью
_UNITS = [["", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять"],
          ["", "одна", "две", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять"]]
_TEENS = ["десять", "одиннадцать", "двенадцать", "тринадцать", "четырнадцать", "пятнадцать",
          "шестнадцать", "семнадцать", "восемнадцать", "девятнадцать"]
_TENS = ["", "", "двадцать", "тридцать", "сорок", "пятьдесят", "шестьдесят", "семьдесят", "восемьдесят",
         "девяносто"]
_HUNDREDS = ["", "сто", "двести", "триста", "четыреста", "пятьсот", "шестьсот", "семьсот", "восемьсот",
             "девятьсот"]
_ORDERS = [(("рубль", "рубля", "рублей"), 0), (("тысяча", "тысячи", "тысяч"), 1),
           (("миллион", "миллиона", "миллионов"), 0), (("миллиард", "миллиарда", "миллиардов"), 0),
           (("триллион", "триллиона", "триллионов"), 0)]


def plural(n: int, forms: Tuple[str, str, str]) -> str:
    n = abs(n) % 100
    if 11 <= n <= 19:
        return forms[2]
    n %= 10
    return forms[0] if n == 1 else forms[1] if 2 <= n <= 4 else forms[2]


def _triad(n: int, fem: int) -> List[str]:
    words = [_HUNDREDS[n // 100]]
    rest = n % 100
    if 10 <= rest <= 19:
        words.append(_TEENS[rest - 10])
    else:
        words += [_TENS[rest // 10], _UNITS[fem][rest % 10]]
    return [w for w in words if w]


def amount_in_words(amount: Any, currency: bool = True) -> str:
    """1234.5 → «Одна тысяча двести тридцать четыре рубля 50 копеек»."""
    value = round(float(str(amount).replace(" ", "").replace(",", ".")), 2)
    rub = int(value)
    kop = int(round((value - rub) * 100))
    if rub == 0:
        words = ["ноль"]
        rub_word = "рублей"
    else:
        words, n, order = [], rub, 0
        rub_word = plural(rub, _ORDERS[0][0])
        while n:
            n, tri = divmod(n, 1000)
            if tri and order:
                forms, fem = _ORDERS[order]
                words = _triad(tri, fem) + [plural(tri, forms)] + words
            elif tri:
                words = _triad(tri, 0) + words
            order += 1
    text = " ".join(words)
    if currency:
        text += f" {rub_word} {kop:02d} {plural(kop, ('копейка', 'копейки', 'копеек'))}"
    return text[:1].upper() + text[1:]


# ---------------------------------------------------------------- 1CClientBankExchange
def parse_1c_statement(raw: Any) -> Dict[str, Any]:
    """Разбор файла выписки/платёжек в формате обмена 1С (kl_to_1c.txt)."""
    if isinstance(raw, bytes):
        text = raw.decode("utf-8") if raw.startswith(b"\xef\xbb\xbf") else _decode_1c(raw)
    else:
        text = str(raw)
    lines = [ln.strip() for ln in text.replace("﻿", "").splitlines()]
    if not lines or lines[0] != "1CClientBankExchange":
        raise ValueError("Это не файл обмена 1CClientBankExchange")
    header: Dict[str, str] = {}
    accounts: List[Dict[str, str]] = []
    documents: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    section = ""
    for ln in lines[1:]:
        if not ln:
            continue
        key, _, value = ln.partition("=")
        if key == "СекцияДокумент":
            current, section = {"ВидДокумента": value}, "doc"
        elif key == "КонецДокумента":
            if current is not None:
                documents.append(_typed(current))
            current, section = None, ""
        elif key == "СекцияРасчСчет":
            current, section = {}, "acc"
        elif key == "КонецРасчСчет":
            if current is not None:
                accounts.append(current)
            current, section = None, ""
        elif key == "КонецФайла":
            break
        elif section and current is not None:
            current[key] = value
        else:
            header[key] = value
    return {"header": header, "accounts": accounts, "documents": documents}


def _decode_1c(raw: bytes) -> str:
    head = raw[:400]
    if b"\xd0" in head and b"DOS" not in head:
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            pass
    return raw.decode("cp866" if b"=DOS" in head else "cp1251", errors="replace")


def _typed(doc: Dict[str, Any]) -> Dict[str, Any]:
    if "Сумма" in doc:
        try:
            doc["Сумма"] = float(doc["Сумма"].replace(",", "."))
        except ValueError:
            pass
    return doc


PAYMENT_FIELDS = ["Номер", "Дата", "Сумма", "ПлательщикСчет", "Плательщик", "ПлательщикИНН", "ПлательщикКПП",
                  "ПлательщикРасчСчет", "ПлательщикБанк1", "ПлательщикБИК", "ПлательщикКорсчет",
                  "ПолучательСчет", "Получатель", "ПолучательИНН", "ПолучательКПП", "ПолучательРасчСчет",
                  "ПолучательБанк1", "ПолучательБИК", "ПолучательКорсчет", "ВидОплаты", "Очередность",
                  "НазначениеПлатежа"]


def build_1c_payments(payments: List[Dict[str, Any]], account: str, sender: str = "RPA Vibe Studio") -> str:
    """Файл платёжных поручений для загрузки в клиент-банк (1c_to_kl.txt)."""
    now = dt.datetime.now()
    dates = [str(p.get("Дата") or now.strftime("%d.%m.%Y")) for p in payments] or [now.strftime("%d.%m.%Y")]
    out = ["1CClientBankExchange", "ВерсияФормата=1.03", "Кодировка=Windows", f"Отправитель={sender}",
           "Получатель=", f"ДатаСоздания={now:%d.%m.%Y}", f"ВремяСоздания={now:%H:%M:%S}",
           f"ДатаНачала={min(dates, key=_d)}", f"ДатаКонца={max(dates, key=_d)}", f"РасчСчет={account}",
           "Документ=Платежное поручение"]
    for i, p in enumerate(payments, 1):
        doc = {"Номер": str(i), "Дата": now.strftime("%d.%m.%Y"), "ВидОплаты": "01", "Очередность": "5",
               "ПлательщикСчет": account, "ПлательщикРасчСчет": account}
        doc.update({k: v for k, v in p.items() if v is not None})
        doc["Сумма"] = f"{float(str(doc.get('Сумма', 0)).replace(',', '.')):.2f}"
        doc.setdefault("ПолучательРасчСчет", doc.get("ПолучательСчет", ""))
        out.append("СекцияДокумент=Платежное поручение")
        for key in PAYMENT_FIELDS + [k for k in doc if k not in PAYMENT_FIELDS]:
            if key in doc:
                out.append(f"{key}={str(doc[key]).replace(chr(10), ' ')}")
        out.append("КонецДокумента")
    out.append("КонецФайла")
    return "\r\n".join(out) + "\r\n"


def _d(s: str) -> dt.date:
    try:
        return dt.datetime.strptime(s, "%d.%m.%Y").date()
    except ValueError:
        return dt.date.today()


# ---------------------------------------------------------------- Банк России
CBR = "https://www.cbr.ru"


def cbr_rates(date: Optional[dt.date] = None) -> Dict[str, Any]:
    import xml.etree.ElementTree as ET

    import httpx

    params = {"date_req": date.strftime("%d/%m/%Y")} if date else {}
    r = httpx.get(f"{CBR}/scripts/XML_daily.asp", params=params, timeout=30, follow_redirects=True,
                  headers={"User-Agent": "Mozilla/5.0 (RPA Vibe Studio)"})
    r.raise_for_status()
    root = ET.fromstring(r.content)
    rates = {}
    for v in root.findall("Valute"):
        nominal = int(v.findtext("Nominal") or 1)
        value = float((v.findtext("Value") or "0").replace(",", "."))
        rates[v.findtext("CharCode")] = {"name": v.findtext("Name"), "nominal": nominal, "value": value,
                                         "per_unit": round(value / nominal, 6), "code": v.findtext("NumCode")}
    return {"date": root.get("Date"), "rates": rates}


def cbr_key_rate(start: dt.date, end: dt.date) -> List[Dict[str, Any]]:
    import xml.etree.ElementTree as ET

    import httpx

    body = ('<?xml version="1.0" encoding="utf-8"?><soap12:Envelope '
            'xmlns:soap12="http://www.w3.org/2003/05/soap-envelope"><soap12:Body>'
            f'<KeyRateXML xmlns="http://web.cbr.ru/"><fromDate>{start.isoformat()}</fromDate>'
            f'<ToDate>{end.isoformat()}</ToDate></KeyRateXML></soap12:Body></soap12:Envelope>')
    r = httpx.post(f"{CBR}/DailyInfoWebServ/DailyInfo.asmx", content=body.encode(), timeout=30,
                   headers={"Content-Type": "application/soap+xml; charset=utf-8"})
    r.raise_for_status()
    out = []
    for el in ET.fromstring(r.content).iter():
        if el.tag.rsplit("}", 1)[-1] == "KR":
            date = next((c.text for c in el if c.tag.rsplit("}", 1)[-1] == "DT"), "")
            rate = next((c.text for c in el if c.tag.rsplit("}", 1)[-1] == "Rate"), "0")
            out.append({"date": (date or "")[:10], "rate": float(rate or 0)})
    return sorted(out, key=lambda x: x["date"])


# ---------------------------------------------------------------- проверка по перечням
_LAT = str.maketrans({"a": "а", "b": "в", "c": "с", "e": "е", "h": "н", "k": "к", "m": "м", "o": "о", "p": "р",
                      "t": "т", "x": "х", "y": "у"})


def norm_name(s: Any) -> str:
    s = str(s or "").lower().replace("ё", "е").translate(_LAT)
    s = re.sub(r"\b(ооо|оао|зао|пао|ао|ип|нко|общество с ограниченной ответственностью)\b", " ", s)
    s = re.sub(r"[^\w ]", " ", s)
    return " ".join(sorted(s.split()))


def screen(subjects: List[Dict[str, Any]], watchlist: List[Dict[str, Any]], name_field: str = "name",
           inn_field: str = "inn", list_name_field: str = "name", list_inn_field: str = "inn",
           threshold: float = 0.88) -> List[Dict[str, Any]]:
    """Ищет совпадения клиентов с перечнем: точно по ИНН, нечётко по ФИО/наименованию."""
    by_inn = {_digits(w.get(list_inn_field)): w for w in watchlist if _digits(w.get(list_inn_field))}
    names = [(norm_name(w.get(list_name_field)), w) for w in watchlist if w.get(list_name_field)]
    hits = []
    for s in subjects:
        inn = _digits(s.get(inn_field))
        if inn and inn in by_inn:
            hits.append({"subject": s, "match": by_inn[inn], "by": "inn", "score": 1.0})
            continue
        sn = norm_name(s.get(name_field))
        if not sn:
            continue
        best, best_score = None, 0.0
        for wn, w in names:
            score = difflib.SequenceMatcher(None, sn, wn).ratio()
            if score > best_score:
                best, best_score = w, score
        if best is not None and best_score >= threshold:
            hits.append({"subject": s, "match": best, "by": "name", "score": round(best_score, 3)})
    return hits

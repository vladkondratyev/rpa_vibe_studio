"""Банковские блоки: реквизиты, формат 1С ↔ Клиент-банк, ЦБ РФ, ЕГРЮЛ, 115-ФЗ."""
from __future__ import annotations

import datetime as dt
import os
from typing import Any, Dict

from .. import banking
from ..context import Context
from ..registry import Output, Param, block

P = Dict[str, Any]
CAT = "Банк"


@block(
    "bank.validate", "Проверить реквизит", CAT,
    params=[Param("kind", "Что проверить", "select", default="inn",
                  options=["inn", "snils", "ogrn", "account"]),
            Param("value", "Значение", required=True),
            Param("bik", "БИК (для счёта)")],
    outputs=[Output("valid", "Верно"), Output("invalid", "Ошибка")],
    description="Контрольные суммы ИНН, СНИЛС, ОГРН/ОГРНИП и ключ счёта по БИК.",
)
def validate(ctx: Context, p: P) -> str:
    kind = p["kind"] or "inn"
    ok = {"inn": banking.check_inn, "snils": banking.check_snils, "ogrn": banking.check_ogrn}.get(kind)
    valid = banking.check_account(p["value"], p["bik"]) if kind == "account" else ok(p["value"])
    return "valid" if valid else "invalid"


@block(
    "bank.amount_words", "Сумма прописью", CAT,
    params=[Param("amount", "Сумма", required=True),
            Param("result", "Записать в", "variable", required=True, default="amount_words")],
)
def amount_words(ctx: Context, p: P) -> None:
    ctx.vars[p["result"]] = banking.amount_in_words(p["amount"])


@block(
    "bank.read_1c", "Прочитать выписку 1С", CAT,
    params=[Param("path", "Файл kl_to_1c.txt", required=True),
            Param("result", "Записать в", "variable", required=True, default="statement")],
    description="Формат 1CClientBankExchange: {header, accounts, documents[]} — операции выписки и платёжки.",
)
def read_1c(ctx: Context, p: P) -> None:
    with open(os.path.expanduser(p["path"]), "rb") as f:
        data = banking.parse_1c_statement(f.read())
    ctx.vars[p["result"]] = data
    ctx.log(f"Документов в файле: {len(data['documents'])}")


@block(
    "bank.write_1c", "Сформировать платёжки 1С", CAT,
    params=[Param("payments", "Платежи", "expression", required=True,
                  help="Список словарей с полями формата: Сумма, Получатель, ПолучательИНН, ПолучательСчет, "
                       "ПолучательБИК, НазначениеПлатежа…"),
            Param("account", "Счёт плательщика", required=True),
            Param("path", "Сохранить в", required=True, default="1c_to_kl.txt")],
    description="Файл для загрузки платёжных поручений в любой клиент-банк (Windows-1251).",
)
def write_1c(ctx: Context, p: P) -> None:
    text = banking.build_1c_payments(ctx.eval(p["payments"]) or [], p["account"])
    with open(os.path.expanduser(p["path"]), "wb") as f:
        f.write(text.encode("cp1251", errors="replace"))
    ctx.log(f"Файл платёжек сохранён: {p['path']}")


@block(
    "bank.cbr_rates", "Курсы валют ЦБ РФ", CAT,
    params=[Param("date", "Дата", help="ДД.ММ.ГГГГ; пусто — на сегодня"),
            Param("result", "Записать в", "variable", required=True, default="rates")],
    description="Официальные курсы Банка России: {date, rates: {USD: {value, nominal, per_unit}, …}}.",
)
def cbr_rates(ctx: Context, p: P) -> None:
    date = dt.datetime.strptime(p["date"], "%d.%m.%Y").date() if p["date"] else None
    data = banking.cbr_rates(date)
    ctx.vars[p["result"]] = data
    ctx.log(f"Курсы ЦБ на {data['date']}: валют {len(data['rates'])}")


@block(
    "bank.cbr_key_rate", "Ключевая ставка ЦБ РФ", CAT,
    params=[Param("days", "За последние дней", "number", default=30),
            Param("result", "Записать в", "variable", required=True, default="key_rate")],
    description="Список {date, rate}; последнее значение — текущая ставка.",
)
def cbr_key_rate(ctx: Context, p: P) -> None:
    end = dt.date.today()
    rows = banking.cbr_key_rate(end - dt.timedelta(days=int(p["days"] or 30)), end)
    ctx.vars[p["result"]] = rows
    if rows:
        ctx.log(f"Ключевая ставка на {rows[-1]['date']}: {rows[-1]['rate']}%")


@block(
    "bank.egrul", "Сведения из ЕГРЮЛ/ЕГРИП", CAT,
    params=[Param("query", "ИНН, ОГРН или наименование", required=True),
            Param("result", "Записать в", "variable", required=True, default="egrul")],
    outputs=[Output("found", "Найдено"), Output("not_found", "Не найдено")],
    description="Поиск на egrul.nalog.ru: наименование, ИНН, ОГРН, КПП, руководитель, дата прекращения.",
)
def egrul(ctx: Context, p: P) -> str:
    import time

    import httpx

    with httpx.Client(timeout=30, headers={"User-Agent": "Mozilla/5.0 (RPA Vibe Studio)"}) as c:
        r = c.post("https://egrul.nalog.ru/", data={"query": str(p["query"]).strip()})
        r.raise_for_status()
        token = r.json().get("t")
        rows = []
        for _ in range(20):
            res = c.get(f"https://egrul.nalog.ru/search-result/{token}")
            if res.status_code == 200 and res.json().get("rows") is not None:
                rows = res.json()["rows"]
                break
            time.sleep(0.7)
    items = [{"name": x.get("n"), "short_name": x.get("c"), "inn": x.get("i"), "ogrn": x.get("o"),
              "kpp": x.get("p"), "director": x.get("g"), "address": x.get("a"), "registered": x.get("r"),
              "terminated": x.get("e") or None, "kind": "ИП" if x.get("k") == "fl" else "ЮЛ"} for x in rows]
    ctx.vars[p["result"]] = items
    if items and items[0]["terminated"]:
        ctx.log(f"Внимание: деятельность прекращена {items[0]['terminated']}", "warning")
    return "found" if items else "not_found"


@block(
    "bank.screen", "Проверка по перечню (115-ФЗ)", CAT,
    params=[Param("subjects", "Клиенты/контрагенты", "expression", required=True),
            Param("watchlist", "Перечень", "expression", required=True,
                  help="Список из Excel/CSV: перечень Росфинмониторинга, санкционные, стоп-листы банка"),
            Param("name_field", "Поле ФИО/наименования клиента", default="name"),
            Param("inn_field", "Поле ИНН клиента", default="inn"),
            Param("list_name_field", "Поле ФИО в перечне", default="name"),
            Param("list_inn_field", "Поле ИНН в перечне", default="inn"),
            Param("threshold", "Порог схожести имён", "number", default=0.88),
            Param("result", "Совпадения в", "variable", required=True, default="hits")],
    outputs=[Output("hits", "Есть совпадения"), Output("clean", "Чисто")],
    description="Точное совпадение по ИНН и нечёткое по ФИО/наименованию (ё/е, латиница, ОПФ, порядок слов).",
)
def screen(ctx: Context, p: P) -> str:
    hits = banking.screen(ctx.eval(p["subjects"]) or [], ctx.eval(p["watchlist"]) or [],
                          p["name_field"] or "name", p["inn_field"] or "inn", p["list_name_field"] or "name",
                          p["list_inn_field"] or "inn", float(p["threshold"] or 0.88))
    ctx.vars[p["result"]] = hits
    if hits:
        ctx.log(f"Совпадений с перечнем: {len(hits)}", "warning")
    return "hits" if hits else "clean"

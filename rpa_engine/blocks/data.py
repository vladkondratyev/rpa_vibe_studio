"""Данные: SQL-базы (PostgreSQL, MS SQL, Oracle, MySQL, SQLite), текст,
регулярные выражения, таблицы."""
from __future__ import annotations

import datetime as dt
import decimal
import json
import re
from typing import Any, Dict, List

from ..context import Context
from ..registry import Param, block

P = Dict[str, Any]
CAT = "Данные"

CONN = Param("connection", "Подключение (URL SQLAlchemy)", required=True,
             help="postgresql+psycopg://user@host/db, mssql+pyodbc://…, oracle+oracledb://…, sqlite:///file.db")
CONN_PWD = Param("password", "Пароль (секрет)", "secret", help="Подставится в URL вместо {password}")


def _engine(ctx: Context, p: P) -> Any:
    from sqlalchemy import create_engine

    url = p["connection"]
    if p.get("password"):
        from urllib.parse import quote_plus

        url = url.replace("{password}", quote_plus(p["password"]))
    cache = ctx.resources.setdefault("_db_engines", {})
    if url not in cache:
        engine = create_engine(url, future=True)
        cache[url] = engine
        ctx.on_cleanup(engine.dispose)
    return cache[url]


def _plain(value: Any) -> Any:
    if isinstance(value, decimal.Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    return value


def _params(raw: Any) -> Dict[str, Any]:
    if raw in (None, ""):
        return {}
    return raw if isinstance(raw, dict) else json.loads(raw)


@block(
    "db.query", "SQL-запрос", CAT,
    params=[CONN, CONN_PWD,
            Param("sql", "Запрос", "text", required=True, help="Параметры — :имя, например WHERE inn = :inn"),
            Param("params", "Параметры (JSON или {{ выражение }})", "string"),
            Param("result", "Строки в", "variable", required=True, default="rows")],
    description="SELECT → список словарей. Драйвер базы ставится отдельно (psycopg, pyodbc, oracledb…).",
)
def query(ctx: Context, p: P) -> None:
    from sqlalchemy import text

    with _engine(ctx, p).connect() as conn:
        res = conn.execute(text(p["sql"]), _params(p["params"]))
        rows = [{k: _plain(v) for k, v in r._mapping.items()} for r in res]
    ctx.vars[p["result"]] = rows
    ctx.log(f"Получено строк: {len(rows)}")


@block(
    "db.execute", "SQL-команда", CAT,
    params=[CONN, CONN_PWD,
            Param("sql", "Команда", "text", required=True, help="INSERT / UPDATE / DELETE / вызов процедуры"),
            Param("params", "Параметры", "string",
                  help="Объект — один раз; список объектов — пакетно для каждой строки"),
            Param("result", "Затронуто строк в", "variable", default="affected")],
)
def execute(ctx: Context, p: P) -> None:
    from sqlalchemy import text

    params = p["params"]
    if isinstance(params, str) and params.strip():
        params = json.loads(params)
    with _engine(ctx, p).begin() as conn:
        res = conn.execute(text(p["sql"]), params or {})
        affected = res.rowcount
    if p["result"]:
        ctx.vars[p["result"]] = affected
    ctx.log(f"Затронуто строк: {affected}")


@block(
    "text.regex", "Найти по шаблону", CAT,
    params=[Param("text", "Текст", required=True),
            Param("pattern", "Регулярное выражение", required=True, help=r"Например ИНН\s*(\d{10,12})"),
            Param("mode", "Что вернуть", "select", default="first", options=["first", "all", "groups", "dict"],
                  help="first — первое совпадение (группа 1, если есть); all — все; groups — список групп; "
                       "dict — именованные группы (?P<имя>…)"),
            Param("ignore_case", "Без учёта регистра", "boolean", default=True),
            Param("result", "Записать в", "variable", required=True, default="match")],
)
def regex(ctx: Context, p: P) -> None:
    flags = re.S | (re.I if p["ignore_case"] else 0)
    rx = re.compile(p["pattern"], flags)
    text = str(p["text"] or "")
    mode = p["mode"] or "first"
    if mode == "all":
        value: Any = rx.findall(text)
    else:
        m = rx.search(text)
        if not m:
            value = None
        elif mode == "groups":
            value = list(m.groups())
        elif mode == "dict":
            value = m.groupdict()
        else:
            value = m.group(1) if rx.groups else m.group(0)
    ctx.vars[p["result"]] = value


@block(
    "text.replace", "Заменить в тексте", CAT,
    params=[Param("text", "Текст", required=True), Param("pattern", "Что (регулярное выражение)", required=True),
            Param("replacement", "На что", default=""),
            Param("result", "Записать в", "variable", required=True, default="text")],
)
def replace(ctx: Context, p: P) -> None:
    ctx.vars[p["result"]] = re.sub(p["pattern"], p["replacement"] or "", str(p["text"] or ""))


@block(
    "text.template", "Текст по шаблону", CAT,
    params=[Param("template", "Шаблон", "text", required=True,
                  help="Уважаемый {{ клиент }}! Задолженность {{ '%.2f' % долг }} руб."),
            Param("result", "Записать в", "variable", required=True, default="text")],
    description="Подставляет {{ выражения }} в многострочный текст: письма, ответы, уведомления.",
)
def template(ctx: Context, p: P) -> None:
    ctx.vars[p["result"]] = p["template"] if isinstance(p["template"], str) else str(p["template"])


@block(
    "table.filter", "Отфильтровать таблицу", CAT,
    params=[Param("rows", "Строки", "expression", required=True),
            Param("condition", "Условие для строки", "expression", required=True,
                  help="Переменная row — текущая строка, например row['Сумма'] > 100000"),
            Param("result", "Записать в", "variable", required=True, default="filtered")],
)
def filter_rows(ctx: Context, p: P) -> None:
    rows = ctx.eval(p["rows"]) or []
    out: List[Any] = []
    for row in rows:
        ctx.vars["row"] = row
        if ctx.eval(p["condition"]):
            out.append(row)
    ctx.vars.pop("row", None)
    ctx.vars[p["result"]] = out


@block(
    "table.group_sum", "Сгруппировать и сложить", CAT,
    params=[Param("rows", "Строки", "expression", required=True),
            Param("by", "Группировать по полю", required=True),
            Param("sum", "Суммировать поле", required=True),
            Param("result", "Записать в", "variable", required=True, default="totals")],
    description="Результат: список {поле: значение, сумма: …, количество: …}.",
)
def group_sum(ctx: Context, p: P) -> None:
    acc: Dict[Any, Dict[str, Any]] = {}
    for row in ctx.eval(p["rows"]) or []:
        key = row.get(p["by"])
        g = acc.setdefault(key, {p["by"]: key, "сумма": 0, "количество": 0})
        g["сумма"] += float(row.get(p["sum"]) or 0)
        g["количество"] += 1
    ctx.vars[p["result"]] = sorted(acc.values(), key=lambda g: -g["сумма"])


@block(
    "table.compare", "Сверить две таблицы", CAT,
    params=[Param("left", "Таблица А", "expression", required=True),
            Param("right", "Таблица Б", "expression", required=True),
            Param("key", "Ключевое поле", required=True, help="Можно разные: номер|НомерДокумента"),
            Param("value", "Сравнивать поле", help="Например сумма|Сумма; пусто — только наличие"),
            Param("tolerance", "Допуск", "number", default=0.01),
            Param("result", "Результат в", "variable", required=True, default="reconciliation")],
    description="Сверка: {matched, mismatched, only_left, only_right} — основа сверок выписок и реестров.",
)
def compare(ctx: Context, p: P) -> None:
    lk, _, rk = p["key"].partition("|")
    rk = rk or lk
    lv, _, rv = (p["value"] or "").partition("|")
    rv = rv or lv
    left = {str(r.get(lk)).strip(): r for r in ctx.eval(p["left"]) or []}
    right = {str(r.get(rk)).strip(): r for r in ctx.eval(p["right"]) or []}
    tol = float(p["tolerance"] or 0)
    result: Dict[str, List[Any]] = {"matched": [], "mismatched": [], "only_left": [], "only_right": []}
    for key, a in left.items():
        b = right.get(key)
        if b is None:
            result["only_left"].append(a)
        elif lv and abs(_num(a.get(lv)) - _num(b.get(rv))) > tol:
            result["mismatched"].append({"key": key, "left": a, "right": b,
                                         "difference": round(_num(a.get(lv)) - _num(b.get(rv)), 2)})
        else:
            result["matched"].append({"key": key, "left": a, "right": b})
    result["only_right"] = [b for key, b in right.items() if key not in left]
    ctx.vars[p["result"]] = result
    ctx.log("Сверка: совпало {}, расхождений {}, только в А {}, только в Б {}".format(
        *(len(result[k]) for k in ("matched", "mismatched", "only_left", "only_right"))))


def _num(v: Any) -> float:
    if v is None or v == "":
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    return float(str(v).replace(" ", "").replace(" ", "").replace(",", "."))

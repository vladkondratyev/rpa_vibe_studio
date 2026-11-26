"""Очереди данных и вызов процессов через оркестратор.

Очередь — транзакционный список элементов (заявки, платежи, документы):
несколько роботов разбирают её параллельно, каждый элемент обрабатывается
ровно одним роботом, с повторами при сбое и контролем SLA.
"""
from __future__ import annotations

import json
from typing import Any, Dict

from ..context import Context
from ..registry import Output, Param, block

P = Dict[str, Any]
CAT = "Очереди"
QUEUE = Param("queue", "Очередь", required=True)


@block("queue.add", "Добавить в очередь", CAT,
       params=[QUEUE, Param("data", "Данные элемента", "string", required=True, help="{{ словарь }} или JSON"),
               Param("reference", "Ключ (для поиска и защиты от дублей)"),
               Param("priority", "Приоритет", "number", default=0, help="Больше — раньше"),
               Param("many", "Данные — список элементов", "boolean", default=False)])
def add(ctx: Context, p: P) -> None:
    data = p["data"]
    if isinstance(data, str):
        data = json.loads(data)
    items = data if p["many"] else [data]
    payload = [{"data": d, "reference": (str(d.get(p["reference"])) if p["many"] and isinstance(d, dict) and p["reference"]
                                         and p["reference"] in d else p["reference"] or None),
                "priority": int(p["priority"] or 0)} for d in items]
    added = ctx.need_orchestrator().queue_add(p["queue"], payload)
    ctx.log(f"В очередь «{p['queue']}» добавлено: {added}")


@block("queue.next", "Взять из очереди", CAT,
       params=[QUEUE, Param("item", "Элемент в", "variable", required=True, default="item")],
       outputs=[Output("item", "Есть элемент"), Output("empty", "Очередь пуста")],
       description="Атомарно забирает следующий элемент (по приоритету и времени). Данные — item['data'].")
def next_(ctx: Context, p: P) -> str:
    item = ctx.need_orchestrator().queue_next(p["queue"])
    if item is None:
        return "empty"
    ctx.vars[p["item"]] = item
    ctx.log(f"Взят элемент #{item['id']} {item.get('reference') or ''}".strip())
    return "item"


@block("queue.complete", "Элемент обработан", CAT,
       params=[Param("item", "Элемент", "expression", required=True, default="item"),
               Param("result", "Результат", "string", help="{{ словарь }} — сохранится в элементе")])
def complete(ctx: Context, p: P) -> None:
    item = ctx.eval(p["item"])
    res = p["result"]
    if isinstance(res, str) and res.strip().startswith(("{", "[")):
        res = json.loads(res)
    ctx.need_orchestrator().queue_finish(item["id"], "done", result=res)


@block("queue.fail", "Элемент не обработан", CAT,
       params=[Param("item", "Элемент", "expression", required=True, default="item"),
               Param("error", "Причина", required=True, default="{{ last_error }}"),
               Param("business", "Бизнес-исключение (без повтора)", "boolean", default=False,
                     help="Например: неверный ИНН в заявке. Технические сбои повторяются автоматически")])
def fail(ctx: Context, p: P) -> None:
    item = ctx.eval(p["item"])
    ctx.need_orchestrator().queue_finish(item["id"], "failed", error=str(p["error"]), business=bool(p["business"]))


@block("process.run", "Запустить процесс", CAT,
       params=[Param("name", "Процесс", required=True),
               Param("inputs", "Входные данные", "string", help="{{ словарь }}"),
               Param("wait", "Дождаться завершения", "boolean", default=False),
               Param("result", "Итог в", "variable", default="subprocess")])
def run(ctx: Context, p: P) -> None:
    inputs = p["inputs"] or {}
    if isinstance(inputs, str):
        inputs = json.loads(inputs)
    res = ctx.need_orchestrator().run_process(p["name"], inputs, bool(p["wait"]), ctx.check_cancel)
    if p["result"]:
        ctx.vars[p["result"]] = res
    if p["wait"] and res.get("status") != "success":
        raise RuntimeError(f"Процесс «{p['name']}» завершился: {res.get('status')} {res.get('error') or ''}")

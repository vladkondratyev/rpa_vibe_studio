"""Базовые блоки: старт/конец, переменные, условия, циклы, Python."""
from __future__ import annotations

import time
from typing import Any, Dict

from ..context import Context, ProcessError
from ..registry import Output, Param, block

P = Dict[str, Any]


@block("core.start", "Старт", "Логика", outputs=[Output("out")], error_output=False,
       description="Точка входа процесса. В процессе ровно один такой блок.")
def start(ctx: Context, p: P) -> None:
    return None


@block("core.end", "Конец", "Логика", outputs=[], error_output=False,
       description="Завершает процесс успешно.")
def end(ctx: Context, p: P) -> None:
    return None


@block(
    "core.set_variable", "Присвоить переменную", "Логика",
    params=[
        Param("name", "Переменная", "variable", required=True),
        Param("value", "Значение", "string", help="Текст или {{ выражение }}"),
    ],
)
def set_variable(ctx: Context, p: P) -> None:
    ctx.vars[p["name"]] = p["value"]


@block(
    "core.log", "Записать в лог", "Логика",
    params=[
        Param("message", "Сообщение", "string", required=True),
        Param("level", "Уровень", "select", default="info", options=["info", "warning", "error"]),
    ],
)
def log(ctx: Context, p: P) -> None:
    ctx.log(p["message"], p["level"] or "info")


@block("core.delay", "Пауза", "Логика",
       params=[Param("seconds", "Секунд", "number", required=True, default=1)])
def delay(ctx: Context, p: P) -> None:
    deadline = time.monotonic() + float(p["seconds"])
    while time.monotonic() < deadline:
        ctx.check_cancel()
        time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))


@block(
    "core.if", "Условие", "Логика",
    params=[Param("condition", "Условие", "expression", required=True,
                  help="Python-выражение, например total > 0")],
    outputs=[Output("true", "Да"), Output("false", "Нет")],
)
def if_(ctx: Context, p: P) -> str:
    return "true" if ctx.eval(p["condition"]) else "false"


@block(
    "core.for_each", "Для каждого", "Логика",
    params=[
        Param("collection", "Коллекция", "expression", required=True, help="Например rows или range(10)"),
        Param("item", "Переменная элемента", "variable", required=True, default="item"),
        Param("index", "Переменная индекса", "variable", default="index"),
    ],
    outputs=[Output("body", "Тело"), Output("done", "Готово")],
    description="Повторяет ветку «Тело» для каждого элемента. Конец тела можно не соединять: "
                "выполнение само вернётся в цикл.",
)
def for_each(ctx: Context, p: P) -> str:
    node = ctx.current_node or ""
    state = ctx.loop_state.get(node)
    if state is None:
        state = {"items": list(ctx.eval(p["collection"]) or []), "pos": 0}
        ctx.loop_state[node] = state
    if state["pos"] >= len(state["items"]):
        del ctx.loop_state[node]
        return "done"
    ctx.vars[p["item"]] = state["items"][state["pos"]]
    if p.get("index"):
        ctx.vars[p["index"]] = state["pos"]
    state["pos"] += 1
    return "body"


@block(
    "core.while", "Пока", "Логика",
    params=[
        Param("condition", "Условие", "expression", required=True),
        Param("max_iterations", "Максимум итераций", "number", default=10000),
    ],
    outputs=[Output("body", "Тело"), Output("done", "Готово")],
)
def while_(ctx: Context, p: P) -> str:
    node = ctx.current_node or ""
    count = ctx.loop_state.get(node, 0)
    if not ctx.eval(p["condition"]):
        ctx.loop_state.pop(node, None)
        return "done"
    if count >= int(p["max_iterations"] or 10000):
        ctx.loop_state.pop(node, None)
        raise ProcessError(f"Цикл превысил {p['max_iterations']} итераций")
    ctx.loop_state[node] = count + 1
    return "body"


@block(
    "core.python", "Python-код", "Логика",
    params=[Param("code", "Код", "code", required=True,
                  default="# переменные процесса доступны как обычные имена\n"
                          "# log('сообщение') пишет в журнал\n")],
    description="Произвольная Python-вставка. Новые и изменённые переменные сохраняются в процессе.",
)
def python(ctx: Context, p: P) -> None:
    ctx.exec(p["code"])


@block("core.fail", "Ошибка", "Логика",
       params=[Param("message", "Сообщение", "string", required=True)],
       description="Прерывает процесс с ошибкой.",
       error_output=False)
def fail(ctx: Context, p: P) -> None:
    raise ProcessError(p["message"])

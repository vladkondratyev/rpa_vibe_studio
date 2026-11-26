"""Интерпретатор процесса.

Формат процесса (JSON, его сохраняет дизайнер):

{
  "variables": [{"name": "folder", "default": "C:/in"}],   # входные переменные
  "outputs": ["total"],                                      # что вернуть в итог
  "nodes": [{"id": "n1", "type": "core.start", "params": {}, "position": {...}}],
  "edges": [{"source": "n1", "sourceHandle": "out", "target": "n2"}]
}

Выполнение идёт от блока «Старт» по рёбрам. Блок возвращает имя выхода,
по которому идти дальше. Выход "error" есть у любого блока: если он подключён,
исключение не роняет процесс, а уводит в эту ветку (как try/catch).
Ветка тела цикла, которая заканчивается без ребра, автоматически
возвращается в свой цикл.
"""
from __future__ import annotations

import json
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .context import Cancelled, Context
from .registry import get_block

MAX_STEPS = 1_000_000
NO_RENDER_KINDS = {"code", "expression", "variable", "secret"}


class DefinitionError(Exception):
    """Ошибка в структуре процесса (нет старта, битые рёбра и т.п.)."""


@dataclass
class RunResult:
    status: str  # success | failed | cancelled
    outputs: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    failed_node: Optional[str] = None
    steps: int = 0


def validate(definition: Dict[str, Any]) -> List[str]:
    """Возвращает список ошибок структуры (пустой, если всё в порядке)."""
    errors: List[str] = []
    nodes = definition.get("nodes") or []
    ids = set()
    starts = 0
    for n in nodes:
        nid = n.get("id")
        if not nid:
            errors.append("У блока нет id")
            continue
        if nid in ids:
            errors.append(f"Повторяющийся id блока: {nid}")
        ids.add(nid)
        try:
            bdef = get_block(n.get("type", ""))
        except KeyError as exc:
            errors.append(f"{nid}: {exc.args[0]}")
            continue
        if bdef.type == "core.start":
            starts += 1
        params = n.get("params") or {}
        for p in bdef.params:
            if p.required and _is_empty(params.get(p.name, p.default)):
                errors.append(f"{nid} ({bdef.title}): не заполнен параметр «{p.label}»")
    if starts != 1:
        errors.append("В процессе должен быть ровно один блок «Старт»")
    seen_handles = set()
    for e in definition.get("edges") or []:
        if e.get("source") not in ids or e.get("target") not in ids:
            errors.append(f"Ребро ссылается на несуществующий блок: {e}")
            continue
        key = (e["source"], e.get("sourceHandle") or "out")
        if key in seen_handles:
            errors.append(f"Из выхода «{key[1]}» блока {key[0]} идёт больше одного ребра")
        seen_handles.add(key)
    return errors


def _is_empty(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def run_process(
    definition: Dict[str, Any],
    inputs: Optional[Dict[str, Any]] = None,
    ctx: Optional[Context] = None,
) -> RunResult:
    errors = validate(definition)
    if errors:
        return RunResult(status="failed", error="Процесс содержит ошибки:\n" + "\n".join(errors))

    ctx = ctx or Context()
    for var in definition.get("variables") or []:
        name = var.get("name")
        if name and name not in ctx.vars:
            ctx.vars[name] = var.get("default")
    ctx.vars.update(inputs or {})

    nodes = {n["id"]: n for n in definition["nodes"]}
    edges: Dict[Tuple[str, str], str] = {}
    for e in definition.get("edges") or []:
        edges[(e["source"], e.get("sourceHandle") or "out")] = e["target"]
    current: Optional[str] = next(n["id"] for n in definition["nodes"] if n["type"] == "core.start")

    loop_stack: List[str] = []
    steps = 0
    ctx.log("Процесс запущен")
    try:
        while current is not None:
            ctx.check_cancel()
            steps += 1
            if steps > MAX_STEPS:
                raise RuntimeError(f"Превышен лимит шагов ({MAX_STEPS}): возможно, бесконечный цикл")
            node = nodes[current]
            bdef = get_block(node["type"])
            ctx.current_node = current
            try:
                params = _resolve_params(ctx, bdef, node.get("params") or {})
                handle = bdef.func(ctx, params) or "out"
            except Cancelled:
                raise
            except Exception as exc:
                message = ctx.mask(f"{bdef.title}: {exc}")
                ctx.vars["last_error"] = message
                target = edges.get((current, "error")) if bdef.error_output else None
                ctx.log(message, "error")
                _capture_error_artifacts(ctx)
                if target is None:
                    ctx.log(traceback.format_exc(), "debug")
                    return RunResult(
                        status="failed",
                        error=message,
                        failed_node=current,
                        outputs=_outputs(ctx, definition),
                        steps=steps,
                    )
                current = target
                continue

            if bdef.type == "core.end":
                break
            if handle == "body":
                if not loop_stack or loop_stack[-1] != current:
                    loop_stack.append(current)
            elif handle == "done" and loop_stack and loop_stack[-1] == current:
                loop_stack.pop()

            nxt = edges.get((current, handle))
            if nxt is None and loop_stack:
                nxt = loop_stack[-1]  # конец тела цикла: возвращаемся к циклу
            current = nxt
    except Cancelled as exc:
        ctx.log(str(exc), "warning")
        return RunResult(status="cancelled", error=str(exc), outputs=_outputs(ctx, definition), steps=steps)
    except Exception as exc:
        ctx.log(f"Ошибка движка: {exc}", "error")
        return RunResult(status="failed", error=str(exc), failed_node=ctx.current_node, steps=steps)
    finally:
        ctx.cleanup()

    ctx.log("Процесс завершён успешно")
    return RunResult(status="success", outputs=_outputs(ctx, definition), steps=steps)


def _resolve_params(ctx: Context, bdef: Any, raw: Dict[str, Any]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for p in bdef.params:
        value = raw.get(p.name, p.default)
        if p.kind not in NO_RENDER_KINDS:
            value = ctx.render(value)
        if p.kind == "number" and isinstance(value, str) and value.strip():
            value = float(value) if "." in value else int(value)
        if p.kind == "boolean" and isinstance(value, str):
            value = value.strip().lower() in ("1", "true", "да", "yes")
        if p.kind == "secret" and not _is_empty(value):
            value = ctx.secret(str(value).strip())
        if p.required and _is_empty(value):
            raise ValueError(f"не заполнен параметр «{p.label}»")
        result[p.name] = value
    return result


def _capture_error_artifacts(ctx: Context) -> None:
    for hook in ctx.resources.get("_error_hooks", []):
        try:
            hook()
        except Exception:
            pass


def _outputs(ctx: Context, definition: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for name in definition.get("outputs") or []:
        if name in ctx.vars:
            out[name] = _jsonable(ctx.vars[name])
    return out


def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)


def run_file(path: str, inputs: Optional[Dict[str, Any]] = None) -> RunResult:
    """Локальный запуск процесса из JSON-файла с выводом лога в консоль."""
    with open(path, encoding="utf-8") as f:
        definition = json.load(f)

    def log(level: str, message: str, node: Optional[str]) -> None:
        stamp = time.strftime("%H:%M:%S")
        print(f"{stamp} [{level.upper():7}] {node or '-':>8} | {message}")

    def artifact(name: str, data: bytes, content_type: str, node: Optional[str]) -> None:
        with open(name, "wb") as f:
            f.write(data)
        print(f"  артефакт сохранён: {name}")

    return run_process(definition, inputs, Context(log=log, artifact=artifact))

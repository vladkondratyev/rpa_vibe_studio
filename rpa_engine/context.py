"""Контекст выполнения процесса: переменные, шаблоны, ресурсы, логирование."""
from __future__ import annotations

import builtins
import datetime as _dt
import inspect
import json
import math
import os
import re
import threading
from typing import Any, Callable, Dict, List, Optional

# {{ выражение }} внутри строковых параметров.
_TEMPLATE_RE = re.compile(r"\{\{(.+?)\}\}", re.S)

LogFn = Callable[[str, str, Optional[str]], None]  # (level, message, node_id)
ArtifactFn = Callable[[str, bytes, str, Optional[str]], None]  # (name, data, content_type, node_id)


class Cancelled(Exception):
    """Выполнение остановлено по запросу оператора."""


class ProcessError(Exception):
    """Ошибка процесса, поднятая блоком «Ошибка» или движком."""


def _expr_globals() -> Dict[str, Any]:
    # Выражения и Python-блоки выполняются с полным доступом к Python:
    # процессы пишут доверенные инженеры, как и вставки кода в Sherpa.
    return {
        "__builtins__": builtins,
        "json": json,
        "math": math,
        "os": os,
        "re": re,
        "datetime": _dt,
        "now": _dt.datetime.now,
        "today": _dt.date.today,
    }


class Context:
    def __init__(
        self,
        variables: Optional[Dict[str, Any]] = None,
        log: Optional[LogFn] = None,
        artifact: Optional[ArtifactFn] = None,
    ) -> None:
        self.vars: Dict[str, Any] = dict(variables or {})
        self._log = log
        self._artifact = artifact
        self.resources: Dict[str, Any] = {}
        self._cleanups: List[Callable[[], None]] = []
        self.current_node: Optional[str] = None
        self.cancel_event = threading.Event()
        # Состояние циклов: node_id -> данные итератора.
        self.loop_state: Dict[str, Any] = {}

    # ---------- логирование и артефакты ----------
    def log(self, message: Any, level: str = "info") -> None:
        if self._log:
            self._log(level, str(message), self.current_node)

    def add_artifact(self, name: str, data: bytes, content_type: str = "image/png") -> None:
        if self._artifact:
            self._artifact(name, data, content_type, self.current_node)

    # ---------- ресурсы (браузер, книги Excel) ----------
    def on_cleanup(self, fn: Callable[[], None]) -> None:
        self._cleanups.append(fn)

    def cleanup(self) -> None:
        while self._cleanups:
            fn = self._cleanups.pop()
            try:
                fn()
            except Exception as exc:  # освобождение ресурсов не должно ронять итог
                self.log(f"Ошибка при освобождении ресурса: {exc}", "warning")

    def check_cancel(self) -> None:
        if self.cancel_event.is_set():
            raise Cancelled("Остановлено оператором")

    # ---------- выражения ----------
    def eval(self, expression: str) -> Any:
        expression = expression.strip()
        if not expression:
            return None
        g = _expr_globals()
        return eval(compile(expression, "<выражение>", "eval"), g, dict(self.vars))

    def render(self, value: Any) -> Any:
        """Подставляет {{ выражения }} в строку.

        Если строка целиком состоит из одного {{ выражения }}, возвращается
        его значение как есть (список, число и т.д.), иначе строка.
        """
        if not isinstance(value, str) or "{{" not in value:
            return value
        whole = _TEMPLATE_RE.fullmatch(value.strip())
        if whole and "}}" not in whole.group(1):  # ровно одно выражение, а не «{{ a }} … {{ b }}»
            return self.eval(whole.group(1))
        return _TEMPLATE_RE.sub(lambda m: _to_text(self.eval(m.group(1))), value)

    def exec(self, code: str) -> None:
        """Выполняет Python-вставку. Переменные процесса доступны как обычные
        имена; новые и изменённые имена записываются обратно в переменные."""
        helpers = _expr_globals()
        helpers.update({"log": self.log, "ctx": self, "vars": self.vars})
        namespace: Dict[str, Any] = dict(helpers)
        namespace.update(self.vars)
        exec(compile(code, "<python-блок>", "exec"), namespace)
        for key, val in namespace.items():
            if key.startswith("_"):
                continue
            if key in helpers and val is helpers[key]:
                continue  # служебные имена не трогаем
            if inspect.ismodule(val) or inspect.isfunction(val) or inspect.isclass(val):
                continue  # импорты и объявленные во вставке функции не переменные
            self.vars[key] = val


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)

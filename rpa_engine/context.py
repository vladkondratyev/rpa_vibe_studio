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


class SecretNotFound(KeyError):
    pass


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
        secrets: Optional[Dict[str, str]] = None,
        orchestrator: Any = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.vars: Dict[str, Any] = dict(variables or {})
        self._log = log
        # Секреты из хранилища оркестратора: доступны как secret('имя'),
        # значения вырезаются из журнала.
        self.secrets: Dict[str, str] = dict(secrets or {})
        # Клиент оркестратора (очереди, запуск процессов); None при локальном запуске.
        self.orchestrator = orchestrator
        # Настройки платформы, которые робот получает с задачей: подключения
        # к моделям ИИ ("ai"), шаблоны документов OCR ("ocr_templates").
        self.config: Dict[str, Any] = dict(config or {})
        for conn in (self.config.get("ai") or {}).values():
            if conn.get("api_key"):
                self.secrets.setdefault(f"__ai_{conn.get('name')}", conn["api_key"])
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
            self._log(level, self.mask(str(message)), self.current_node)

    def mask(self, text: str) -> str:
        for value in self.secrets.values():
            if value and len(value) >= 3:
                text = text.replace(value, "***")
        return text

    def secret(self, name: str) -> str:
        """Значение секрета: из хранилища оркестратора, а при локальном
        запуске — из переменной окружения RPA_SECRET_<ИМЯ>."""
        if name in self.secrets:
            return self.secrets[name]
        env = os.environ.get("RPA_SECRET_" + re.sub(r"\W", "_", name).upper())
        if env is not None:
            self.secrets[name] = env
            return env
        raise SecretNotFound(f"Секрет «{name}» не найден в хранилище")

    def ai_connection(self, name: str = "") -> Dict[str, Any]:
        conns = self.config.get("ai") or {}
        if name:
            if name in conns:
                return conns[name]
            raise KeyError(f"Подключение к модели ИИ «{name}» не настроено (раздел «Модели ИИ»)")
        default = next((c for c in conns.values() if c.get("is_default")), None) or next(iter(conns.values()), None)
        if default:
            return default
        from .llm import env_connection

        env = env_connection()
        if env:
            return env
        raise KeyError("Нет ни одного подключения к модели ИИ: добавьте его в разделе «Модели ИИ»")

    def need_orchestrator(self) -> Any:
        if self.orchestrator is None:
            raise RuntimeError("Блок работает только при запуске через оркестратор (очереди, процессы)")
        return self.orchestrator

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
        g["secret"] = self.secret
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
        helpers.update({"log": self.log, "ctx": self, "vars": self.vars, "secret": self.secret})
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

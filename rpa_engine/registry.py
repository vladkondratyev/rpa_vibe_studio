"""Реестр блоков (активностей), из которых собираются процессы.

Каждый блок описывается декоратором @block: тип, название, категория,
параметры (для формы свойств в дизайнере) и выходы (ветки графа).
Функция блока получает контекст выполнения и вычисленные параметры
и возвращает имя выхода, по которому идёт дальше (None = "out").
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional

# Виды параметров, которые умеет показывать дизайнер.
PARAM_KINDS = {
    "string",    # однострочный текст, поддерживает {{ выражения }}
    "text",      # многострочный текст
    "code",      # Python-код (не подставляется шаблоном)
    "expression",  # Python-выражение (вычисляется целиком)
    "number",
    "boolean",
    "select",    # выбор из options
    "variable",  # имя переменной, куда записать результат
}


@dataclass
class Param:
    name: str
    label: str
    kind: str = "string"
    required: bool = False
    default: Any = None
    options: Optional[List[str]] = None
    help: str = ""

    def __post_init__(self) -> None:
        if self.kind not in PARAM_KINDS:
            raise ValueError(f"Неизвестный вид параметра: {self.kind}")


@dataclass
class Output:
    name: str
    label: str = ""


@dataclass
class BlockDef:
    type: str
    title: str
    category: str
    func: Callable[..., Optional[str]]
    description: str = ""
    params: List[Param] = field(default_factory=list)
    outputs: List[Output] = field(default_factory=lambda: [Output("out")])
    # Можно ли подключить ветку "ошибка" (try/catch на уровне блока).
    error_output: bool = True

    def catalog_entry(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "title": self.title,
            "category": self.category,
            "description": self.description,
            "params": [asdict(p) for p in self.params],
            "outputs": [asdict(o) for o in self.outputs],
            "error_output": self.error_output,
        }


_REGISTRY: Dict[str, BlockDef] = {}


def block(
    type: str,
    title: str,
    category: str,
    params: Optional[List[Param]] = None,
    outputs: Optional[List[Output]] = None,
    description: str = "",
    error_output: bool = True,
) -> Callable[[Callable[..., Optional[str]]], Callable[..., Optional[str]]]:
    def decorator(func: Callable[..., Optional[str]]) -> Callable[..., Optional[str]]:
        if type in _REGISTRY:
            raise ValueError(f"Блок {type} уже зарегистрирован")
        _REGISTRY[type] = BlockDef(
            type=type,
            title=title,
            category=category,
            func=func,
            description=description or (func.__doc__ or "").strip(),
            params=params or [],
            outputs=outputs or [Output("out")],
            error_output=error_output,
        )
        return func

    return decorator


def get_block(type: str) -> BlockDef:
    _ensure_loaded()
    try:
        return _REGISTRY[type]
    except KeyError:
        raise KeyError(f"Неизвестный блок: {type}") from None


def catalog() -> List[Dict[str, Any]]:
    _ensure_loaded()
    return [b.catalog_entry() for b in _REGISTRY.values()]


_loaded = False


def _ensure_loaded() -> None:
    """Импортирует встроенные модули блоков (регистрация через декоратор)."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    from .blocks import core, excel, files, web  # noqa: F401

"""Блоки Excel (.xlsx) на openpyxl — не требуют установленного Excel, работают на Linux."""
from __future__ import annotations

import os
from typing import Any, Dict, List

from ..context import Context
from ..registry import Param, block

P = Dict[str, Any]
CAT = "Excel"

BOOK = Param("book", "Книга (псевдоним)", default="book", help="Имя, под которым открыта книга")
SHEET = Param("sheet", "Лист", help="Пусто = активный лист")


def _book(ctx: Context, alias: str) -> Dict[str, Any]:
    key = f"excel:{alias or 'book'}"
    if key not in ctx.resources:
        raise RuntimeError(f"Книга «{alias}» не открыта: добавьте блок «Открыть книгу»")
    return ctx.resources[key]


def _sheet(ctx: Context, p: P, create: bool = False) -> Any:
    wb = _book(ctx, p["book"])["wb"]
    name = p.get("sheet")
    if not name:
        return wb.active
    if name not in wb.sheetnames:
        if not create:
            raise KeyError(f"Лист «{name}» не найден. Есть: {', '.join(wb.sheetnames)}")
        return wb.create_sheet(name)
    return wb[name]


@block(
    "excel.open", "Открыть книгу", CAT,
    params=[
        Param("path", "Файл", required=True),
        BOOK,
        Param("create", "Создать, если нет", "boolean", default=False),
        Param("values_only", "Читать результаты формул", "boolean", default=False,
              help="Книга открывается только для чтения: формулы заменятся значениями"),
    ],
)
def open_book(ctx: Context, p: P) -> None:
    import openpyxl

    path = os.path.expanduser(p["path"])
    if os.path.exists(path):
        wb = openpyxl.load_workbook(path, data_only=bool(p["values_only"]))
    elif p["create"]:
        wb = openpyxl.Workbook()
    else:
        raise FileNotFoundError(f"Файл не найден: {path}")
    key = f"excel:{p['book'] or 'book'}"
    ctx.resources[key] = {"wb": wb, "path": path, "readonly": bool(p["values_only"])}
    ctx.on_cleanup(lambda: wb.close())


@block(
    "excel.read_cell", "Прочитать ячейку", CAT,
    params=[BOOK, SHEET, Param("cell", "Ячейка", required=True, default="A1"),
            Param("result", "Записать в", "variable", required=True, default="value")],
)
def read_cell(ctx: Context, p: P) -> None:
    ctx.vars[p["result"]] = _sheet(ctx, p)[p["cell"]].value


@block(
    "excel.write_cell", "Записать ячейку", CAT,
    params=[BOOK, SHEET, Param("cell", "Ячейка", required=True, default="A1"), Param("value", "Значение")],
)
def write_cell(ctx: Context, p: P) -> None:
    _sheet(ctx, p, create=True)[p["cell"]] = p["value"]


@block(
    "excel.read_range", "Прочитать диапазон", CAT,
    params=[BOOK, SHEET, Param("range", "Диапазон", help="Например A1:D20; пусто = весь лист"),
            Param("result", "Записать в", "variable", required=True, default="rows")],
    description="Список строк, каждая строка — список значений.",
)
def read_range(ctx: Context, p: P) -> None:
    ws = _sheet(ctx, p)
    cells = ws[p["range"]] if p.get("range") else tuple(ws.iter_rows())
    if not isinstance(cells, tuple):
        cells = ((cells,),)  # одна ячейка
    elif cells and not isinstance(cells[0], tuple):
        cells = (cells,)  # целая строка или столбец ("1:1", "A:A")
    ctx.vars[p["result"]] = [[c.value for c in row] for row in cells]


@block(
    "excel.read_table", "Прочитать таблицу", CAT,
    params=[BOOK, SHEET, Param("header_row", "Строка заголовка", "number", default=1),
            Param("result", "Записать в", "variable", required=True, default="rows")],
    description="Строки под заголовком как список словарей {заголовок: значение}. Пустые строки пропускаются.",
)
def read_table(ctx: Context, p: P) -> None:
    ws = _sheet(ctx, p)
    header_row = int(p["header_row"] or 1)
    rows = ws.iter_rows(min_row=header_row, values_only=True)
    header = next(rows, None) or ()
    keys = [str(h) if h is not None else f"col{i + 1}" for i, h in enumerate(header)]
    result: List[Dict[str, Any]] = []
    for row in rows:
        if all(v is None for v in row):
            continue
        result.append(dict(zip(keys, row)))
    ctx.vars[p["result"]] = result


@block(
    "excel.append_row", "Добавить строку", CAT,
    params=[BOOK, SHEET, Param("values", "Значения", "expression", required=True,
                               help="Список, например [name, total]")],
)
def append_row(ctx: Context, p: P) -> None:
    values = ctx.eval(p["values"])
    if isinstance(values, dict):
        values = list(values.values())
    _sheet(ctx, p, create=True).append(list(values))


@block(
    "excel.write_table", "Записать таблицу", CAT,
    params=[BOOK, SHEET, Param("rows", "Строки", "expression", required=True, help="Список словарей"),
            Param("start", "Начальная ячейка", default="A1"),
            Param("header", "С заголовком", "boolean", default=True)],
)
def write_table(ctx: Context, p: P) -> None:
    from openpyxl.utils.cell import coordinate_from_string, column_index_from_string

    rows = list(ctx.eval(p["rows"]) or [])
    ws = _sheet(ctx, p, create=True)
    col_letter, row0 = coordinate_from_string(p["start"] or "A1")
    col0 = column_index_from_string(col_letter)
    keys: List[str] = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    r_i = row0
    if p["header"]:
        for j, k in enumerate(keys):
            ws.cell(row=r_i, column=col0 + j, value=k)
        r_i += 1
    for r in rows:
        for j, k in enumerate(keys):
            ws.cell(row=r_i, column=col0 + j, value=r.get(k))
        r_i += 1


@block(
    "excel.sheets", "Список листов", CAT,
    params=[BOOK, Param("result", "Записать в", "variable", required=True, default="sheets")],
)
def sheets(ctx: Context, p: P) -> None:
    ctx.vars[p["result"]] = list(_book(ctx, p["book"])["wb"].sheetnames)


@block(
    "excel.save", "Сохранить книгу", CAT,
    params=[BOOK, Param("path", "Сохранить как", help="Пусто = в тот же файл")],
)
def save(ctx: Context, p: P) -> None:
    book = _book(ctx, p["book"])
    if book["readonly"]:
        raise RuntimeError("Книга открыта с «Читать результаты формул»: сохранение уничтожит формулы")
    path = os.path.expanduser(p.get("path") or book["path"])
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    book["wb"].save(path)

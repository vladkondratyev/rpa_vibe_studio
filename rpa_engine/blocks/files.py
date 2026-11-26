"""Блоки работы с файлами и папками."""
from __future__ import annotations

import csv
import glob
import os
import shutil
from typing import Any, Dict

from ..context import Context
from ..registry import Output, Param, block

P = Dict[str, Any]
CAT = "Файлы"


@block(
    "files.list", "Список файлов", CAT,
    params=[
        Param("folder", "Папка", required=True),
        Param("pattern", "Маска", default="*", help="Например *.xlsx или **/*.pdf"),
        Param("result", "Записать в", "variable", required=True, default="files"),
    ],
)
def list_files(ctx: Context, p: P) -> None:
    pattern = os.path.join(os.path.expanduser(p["folder"]), p["pattern"] or "*")
    ctx.vars[p["result"]] = sorted(f for f in glob.glob(pattern, recursive=True) if os.path.isfile(f))


@block(
    "files.exists", "Файл существует?", CAT,
    params=[Param("path", "Путь", required=True)],
    outputs=[Output("true", "Да"), Output("false", "Нет")],
)
def exists(ctx: Context, p: P) -> str:
    return "true" if os.path.exists(os.path.expanduser(p["path"])) else "false"


@block("files.create_folder", "Создать папку", CAT, params=[Param("path", "Путь", required=True)])
def create_folder(ctx: Context, p: P) -> None:
    os.makedirs(os.path.expanduser(p["path"]), exist_ok=True)


@block(
    "files.copy", "Копировать", CAT,
    params=[Param("source", "Откуда", required=True), Param("target", "Куда", required=True),
            Param("overwrite", "Перезаписывать", "boolean", default=True)],
)
def copy(ctx: Context, p: P) -> None:
    src, dst = os.path.expanduser(p["source"]), os.path.expanduser(p["target"])
    _check_target(dst, src, p["overwrite"])
    if os.path.isdir(src):
        shutil.copytree(src, dst, dirs_exist_ok=bool(p["overwrite"]))
    else:
        shutil.copy2(src, dst)


@block(
    "files.move", "Переместить", CAT,
    params=[Param("source", "Откуда", required=True), Param("target", "Куда", required=True),
            Param("overwrite", "Перезаписывать", "boolean", default=False)],
)
def move(ctx: Context, p: P) -> None:
    src, dst = os.path.expanduser(p["source"]), os.path.expanduser(p["target"])
    _check_target(dst, src, p["overwrite"])
    shutil.move(src, dst)


@block("files.delete", "Удалить", CAT, params=[Param("path", "Путь", required=True)])
def delete(ctx: Context, p: P) -> None:
    path = os.path.expanduser(p["path"])
    if os.path.isdir(path):
        shutil.rmtree(path)
    elif os.path.exists(path):
        os.remove(path)


@block(
    "files.read_text", "Прочитать текст", CAT,
    params=[Param("path", "Путь", required=True), Param("encoding", "Кодировка", default="utf-8"),
            Param("result", "Записать в", "variable", required=True, default="text")],
)
def read_text(ctx: Context, p: P) -> None:
    with open(os.path.expanduser(p["path"]), encoding=p["encoding"] or "utf-8") as f:
        ctx.vars[p["result"]] = f.read()


@block(
    "files.write_text", "Записать текст", CAT,
    params=[Param("path", "Путь", required=True), Param("content", "Текст", "text"),
            Param("append", "Дописать в конец", "boolean", default=False),
            Param("encoding", "Кодировка", default="utf-8")],
)
def write_text(ctx: Context, p: P) -> None:
    path = os.path.expanduser(p["path"])
    _ensure_parent(path)
    with open(path, "a" if p["append"] else "w", encoding=p["encoding"] or "utf-8") as f:
        f.write("" if p["content"] is None else str(p["content"]))


@block(
    "files.read_csv", "Прочитать CSV", CAT,
    params=[Param("path", "Путь", required=True), Param("delimiter", "Разделитель", default=";"),
            Param("encoding", "Кодировка", default="utf-8-sig"),
            Param("result", "Записать в", "variable", required=True, default="rows")],
    description="Читает CSV с заголовком в список словарей.",
)
def read_csv(ctx: Context, p: P) -> None:
    with open(os.path.expanduser(p["path"]), encoding=p["encoding"] or "utf-8-sig", newline="") as f:
        ctx.vars[p["result"]] = list(csv.DictReader(f, delimiter=p["delimiter"] or ";"))


@block(
    "files.write_csv", "Записать CSV", CAT,
    params=[Param("path", "Путь", required=True), Param("rows", "Строки", "expression", required=True,
                                                         help="Список словарей"),
            Param("delimiter", "Разделитель", default=";"), Param("encoding", "Кодировка", default="utf-8-sig")],
)
def write_csv(ctx: Context, p: P) -> None:
    rows = list(ctx.eval(p["rows"]) or [])
    path = os.path.expanduser(p["path"])
    _ensure_parent(path)
    fields: list = []
    for r in rows:
        for k in r:
            if k not in fields:
                fields.append(k)
    with open(path, "w", encoding=p["encoding"] or "utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, delimiter=p["delimiter"] or ";")
        w.writeheader()
        w.writerows(rows)


def _ensure_parent(path: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def _check_target(dst: str, src: str, overwrite: Any) -> None:
    if not os.path.exists(src):
        raise FileNotFoundError(f"Не найден: {src}")
    target = os.path.join(dst, os.path.basename(src)) if os.path.isdir(dst) and not os.path.isdir(src) else dst
    if os.path.exists(target) and not overwrite:
        raise FileExistsError(f"Уже существует: {target}")
    _ensure_parent(dst)

"""Документы: текст из PDF, заполнение шаблонов Word."""
from __future__ import annotations

import os
import re
from typing import Any, Dict

from ..context import Context
from ..registry import Param, block

P = Dict[str, Any]
CAT = "Документы"


def pdf_text(path: str, pages: str = "") -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        raise RuntimeError("Нужен пакет pypdf: pip install pypdf") from None
    reader = PdfReader(os.path.expanduser(path))
    selected = _pages(pages, len(reader.pages))
    return "\n\f".join((reader.pages[i].extract_text() or "") for i in selected)


def _pages(spec: str, total: int) -> list:
    if not spec:
        return list(range(total))
    out = []
    for part in str(spec).split(","):
        a, _, b = part.strip().partition("-")
        start = int(a) - 1
        end = int(b) if b else start + 1
        out.extend(i for i in range(start, min(end, total)) if i >= 0)
    return out


@block(
    "docs.pdf_text", "Текст из PDF", CAT,
    params=[Param("path", "Файл PDF", required=True), Param("pages", "Страницы", help="1-3,5; пусто — все"),
            Param("result", "Записать в", "variable", required=True, default="text")],
    description="Текстовый слой PDF. Для сканов — блок «Распознать текст (OCR)» или AI-извлечение.",
)
def read_pdf(ctx: Context, p: P) -> None:
    text = pdf_text(p["path"], p["pages"] or "")
    ctx.vars[p["result"]] = text
    if not text.strip():
        ctx.log("В PDF нет текстового слоя (скан?)", "warning")


@block(
    "docs.word_fill", "Заполнить шаблон Word", CAT,
    params=[Param("template", "Шаблон .docx", required=True),
            Param("output", "Сохранить как", required=True),
            Param("values", "Значения", "expression", required=True,
                  help="Словарь: {'клиент': клиент, 'сумма': сумма} — в шаблоне пишется {клиент}")],
    description="Подставляет значения вместо {меток} в тексте, таблицах и колонтитулах документа.",
)
def word_fill(ctx: Context, p: P) -> None:
    try:
        import docx
    except ImportError:
        raise RuntimeError("Нужен пакет python-docx: pip install python-docx") from None
    values = {str(k): "" if v is None else str(v) for k, v in (ctx.eval(p["values"]) or {}).items()}
    document = docx.Document(os.path.expanduser(p["template"]))

    def fix(paragraph: Any) -> None:
        full = "".join(r.text for r in paragraph.runs)
        if "{" not in full:
            return
        new = re.sub(r"\{([^{}]+)\}", lambda m: values.get(m.group(1).strip(), m.group(0)), full)
        if new != full and paragraph.runs:
            paragraph.runs[0].text = new  # форматирование — по первому фрагменту абзаца
            for r in paragraph.runs[1:]:
                r.text = ""

    def walk(container: Any) -> None:
        for para in container.paragraphs:
            fix(para)
        for table in getattr(container, "tables", []):
            for row in table.rows:
                for cell in row.cells:
                    walk(cell)

    walk(document)
    for section in document.sections:
        for part in (section.header, section.footer):
            walk(part)
    out = os.path.expanduser(p["output"])
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    document.save(out)
    ctx.log(f"Документ сохранён: {out}")


@block(
    "docs.word_text", "Текст из Word", CAT,
    params=[Param("path", "Файл .docx", required=True),
            Param("result", "Записать в", "variable", required=True, default="text")],
)
def word_text(ctx: Context, p: P) -> None:
    import docx

    document = docx.Document(os.path.expanduser(p["path"]))
    parts = [para.text for para in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            parts.append("\t".join(c.text for c in row.cells))
    ctx.vars[p["result"]] = "\n".join(parts)

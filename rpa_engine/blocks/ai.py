"""Блоки искусственного интеллекта поверх подключений «Модели ИИ»:
Claude, YandexGPT, GigaChat, локальные модели (Ollama, vLLM)."""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List

from .. import llm
from ..context import Context
from ..registry import Param, block

P = Dict[str, Any]
CAT = "ИИ"
MODEL = Param("model", "Подключение", help="Имя из раздела «Модели ИИ»; пусто — подключение по умолчанию")


def _images(ctx: Context, raw: Any) -> List[bytes]:
    if not raw:
        return []
    paths = raw if isinstance(raw, list) else [raw]
    out = []
    for path in paths:
        from ..ocr import load_pages

        pages, _ = load_pages(path)
        out.extend(pages)
    return out


@block(
    "ai.ask", "Спросить модель", CAT,
    params=[MODEL, Param("prompt", "Запрос", "text", required=True),
            Param("system", "Роль / инструкция", "text"),
            Param("files", "Изображения или PDF", help="Путь или {{ список путей }}"),
            Param("json", "Ответ — JSON", "boolean", default=False),
            Param("result", "Ответ в", "variable", required=True, default="answer")],
)
def ask(ctx: Context, p: P) -> None:
    conn = ctx.ai_connection(p["model"] or "")
    prompt = p["prompt"] if isinstance(p["prompt"], str) else json.dumps(p["prompt"], ensure_ascii=False)
    if p["json"]:
        prompt += "\n\nОтветь только JSON без пояснений."
    text = llm.chat(conn, prompt, p["system"] or "", _images(ctx, p["files"]))
    ctx.vars[p["result"]] = llm.extract_json(text) if p["json"] else text.strip()
    ctx.log(f"Ответ модели {conn.get('name') or conn.get('provider')}: {len(text)} символов")


@block(
    "ai.extract", "Извлечь поля (ИИ)", CAT,
    params=[MODEL, Param("text", "Текст документа"),
            Param("files", "Скан/фото/PDF", help="Для мультимодальных моделей"),
            Param("fields", "Поля", "text", required=True,
                  default="номер: номер документа\nдата: дата документа\nсумма: итоговая сумма\nинн: ИНН контрагента",
                  help="По строке на поле: имя: описание"),
            Param("result", "Записать в", "variable", required=True, default="fields")],
    description="Счета, договоры, письма, анкеты: словарь значений по описанию полей.",
)
def extract(ctx: Context, p: P) -> None:
    from ..ocr import extract_with_ai

    fields = []
    for line in str(p["fields"]).splitlines():
        name, _, label = line.partition(":")
        if name.strip():
            fields.append({"name": name.strip(), "label": label.strip() or name.strip()})
    res = extract_with_ai(ctx.ai_connection(p["model"] or ""), {"name": "документ", "fields": fields},
                          str(p["text"] or ""), _images(ctx, p["files"]))
    ctx.vars[p["result"]] = res["fields"]


@block(
    "ai.classify", "Классифицировать (ИИ)", CAT,
    params=[MODEL, Param("text", "Текст", required=True),
            Param("categories", "Категории", "text", required=True,
                  default="жалоба\nзапрос выписки\nблокировка карты\nкредит\nдругое", help="По строке на категорию"),
            Param("result", "Категория в", "variable", required=True, default="category")],
    description="Обращения клиентов, письма, заявки: одна категория из списка.",
)
def classify(ctx: Context, p: P) -> None:
    cats = [c.strip() for c in str(p["categories"]).splitlines() if c.strip()]
    text = llm.chat(ctx.ai_connection(p["model"] or ""),
                    "Отнеси текст к одной категории из списка. Ответь только названием категории.\n"
                    "Категории:\n" + "\n".join(f"- {c}" for c in cats) + f"\n\nТекст:\n{str(p['text'])[:20000]}",
                    max_tokens=2000)
    answer = text.strip().strip(".«»\"'").lower()
    ctx.vars[p["result"]] = next((c for c in cats if c.lower() == answer), None) or next(
        (c for c in cats if c.lower() in answer), cats[-1] if cats else answer)


@block(
    "ai.summarize", "Кратко пересказать (ИИ)", CAT,
    params=[MODEL, Param("text", "Текст", required=True),
            Param("style", "Как", default="5 пунктов для руководителя"),
            Param("result", "Записать в", "variable", required=True, default="summary")],
)
def summarize(ctx: Context, p: P) -> None:
    ctx.vars[p["result"]] = llm.chat(ctx.ai_connection(p["model"] or ""),
                                     f"Перескажи кратко ({p['style']}):\n\n{str(p['text'])[:100000]}").strip()

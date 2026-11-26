"""Блоки встроенного модуля OCR: распознавание, определение типа документа,
извлечение полей по шаблонам (паспорт, СНИЛС, ВУ, платёжка, счёт, ЕГРЮЛ…)."""
from __future__ import annotations

from typing import Any, Dict, List

from .. import ocr
from ..context import Context
from ..registry import Output, Param, block

P = Dict[str, Any]
CAT = "OCR · Документы"

FILE = Param("file", "Файл (JPG, PNG, PDF)", required=True)
ENGINE = Param("engine", "Движок", "select", default="tesseract", options=["tesseract", "yandex", "ai"],
               help="tesseract — локально; yandex — Yandex Vision OCR; ai — мультимодальная модель")
YANDEX = Param("yandex", "Подключение Yandex Cloud", help="Имя из «Модели ИИ» с провайдером yandexgpt (ключ и каталог)")
AI = Param("model", "Подключение ИИ (для движка ai)")


def templates(ctx: Context) -> List[Dict[str, Any]]:
    custom = ctx.config.get("ocr_templates") or []
    ids = {t["id"] for t in custom}
    return list(custom) + [t for t in ocr.BUILTIN_TEMPLATES if t["id"] not in ids]


def _conns(ctx: Context, p: P) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    engine = p.get("engine") or "tesseract"
    if engine == "yandex":
        out["yandex_conn"] = ctx.ai_connection(p.get("yandex") or "")
    if engine == "ai":
        out["ai_conn"] = ctx.ai_connection(p.get("model") or "")
    return out


def _recognize_file(ctx: Context, p: P, yandex_model: str = "page") -> List[Dict[str, Any]]:
    pages, text_layer = ocr.load_pages(p["file"])
    engine = p.get("engine") or "tesseract"
    if text_layer.strip() and engine == "tesseract" and not pages:
        return [{"text": text_layer, "words": [], "entities": {}}]
    results = [ocr.recognize(img, engine, p.get("lang") or "rus+eng", yandex_model=yandex_model, **_conns(ctx, p))
               for img in pages]
    if text_layer.strip() and not any(r["text"].strip() for r in results):
        results = [{"text": text_layer, "words": [], "entities": {}}]
    return results


@block(
    "ocr.recognize", "Распознать документ", CAT,
    params=[FILE, ENGINE, Param("lang", "Языки", default="rus+eng"), YANDEX, AI,
            Param("result", "Текст в", "variable", required=True, default="text")],
)
def recognize(ctx: Context, p: P) -> None:
    pages = _recognize_file(ctx, p)
    ctx.vars[p["result"]] = "\n\f".join(r["text"] for r in pages)
    ctx.log(f"Распознано страниц: {len(pages)}, символов: {sum(len(r['text']) for r in pages)}")


@block(
    "ocr.classify", "Определить тип документа", CAT,
    params=[FILE, ENGINE, YANDEX, AI,
            Param("result", "Тип (id шаблона) в", "variable", required=True, default="doc_type")],
    outputs=[Output("known", "Определён"), Output("unknown", "Неизвестен")],
)
def classify(ctx: Context, p: P) -> str:
    pages = _recognize_file(ctx, p)
    tpl, score = ocr.classify("\n".join(r["text"] for r in pages), templates(ctx))
    ctx.vars[p["result"]] = tpl["id"] if tpl and score >= 0.25 else None
    ctx.vars["_ocr_pages"] = pages  # следующему блоку «Извлечь поля» не нужно распознавать заново
    return "known" if ctx.vars[p["result"]] else "unknown"


@block(
    "ocr.extract", "Извлечь поля по шаблону", CAT,
    params=[FILE, Param("template", "Шаблон", required=True, default="auto",
                        help="id шаблона (passport_rf, snils, driver_license, payment_order, invoice, egrul_extract, "
                             "свои — в разделе «OCR-шаблоны») или auto"),
            ENGINE, YANDEX, AI,
            Param("ai_fallback", "Дозаполнить через ИИ", "boolean", default=False,
                  help="Если обязательные поля не найдены — спросить модель"),
            Param("result", "Результат в", "variable", required=True, default="document")],
    outputs=[Output("ok", "Всё распознано"), Output("review", "Нужна проверка")],
    description="Результат: {template, fields, missing, errors, sources}. «Нужна проверка» — если не найдены "
                "обязательные поля или не сошлись контрольные суммы (ИНН, СНИЛС, MRZ).",
)
def extract(ctx: Context, p: P) -> str:
    tpls = templates(ctx)
    tpl_id = (p["template"] or "auto").strip()
    tpl = next((t for t in tpls if t["id"] == tpl_id), None) if tpl_id != "auto" else None
    if tpl_id != "auto" and tpl is None:
        raise ValueError(f"Шаблон «{tpl_id}» не найден")
    engine = p.get("engine") or "tesseract"
    yandex_model = (tpl or {}).get("yandex_model") or "page" if engine == "yandex" else "page"
    pages = _recognize_file(ctx, p, yandex_model)
    if tpl is None:
        tpl, score = ocr.classify("\n".join(r["text"] for r in pages), tpls)
        if tpl is None or score < 0.25:
            ctx.vars[p["result"]] = {"template": None, "fields": {}, "missing": [], "errors": {},
                                     "ok": False, "text": "\n".join(r["text"] for r in pages)}
            ctx.log("Тип документа не определён", "warning")
            return "review"
        ctx.log(f"Тип документа: {tpl['name']} (уверенность {score})")
    res = ocr.extract(pages, tpl)
    if not res["ok"] and p["ai_fallback"]:
        ai_res = ocr.extract_with_ai(ctx.ai_connection(p.get("model") or ""), tpl,
                                     "\n".join(r["text"] for r in pages))
        for k, v in ai_res["fields"].items():
            if not res["fields"].get(k) and v:
                res["fields"][k], res["sources"][k] = v, "ai"
        res["missing"] = [f["name"] for f in tpl["fields"] if f.get("required") and not res["fields"].get(f["name"])]
        res["ok"] = not res["missing"] and not res["errors"]
    res["name"] = tpl.get("name")
    ctx.vars[p["result"]] = res
    if not res["ok"]:
        ctx.log(f"Требуется проверка: нет {res['missing']}, ошибки {res['errors']}", "warning")
    return "ok" if res["ok"] else "review"

"""Встроенный модуль OCR и извлечения полей из документов по шаблонам.

Движки распознавания:
  tesseract — локально, без интернета (Tesseract + русский язык);
  yandex    — Yandex Vision OCR (облако), в т.ч. готовые модели паспорта и ВУ;
  ai        — мультимодальная модель (Claude или OpenAI-совместимая) по шаблону полей.

Шаблон документа — JSON:
{
  "id": "passport_rf", "name": "Паспорт РФ", "keywords": ["паспорт", "выдан"],
  "yandex_model": "passport",                 # необязательно: модель Yandex Vision
  "mrz": true,                                # разобрать машиночитаемую зону
  "fields": [
    {"name": "number", "label": "Серия и номер", "required": true,
     "regex": "(\\d{2}\\s?\\d{2}\\s?\\d{6})",   # поиск по всему тексту (группа 1)
     "anchor": "Фамилия", "anchor_position": "right|below",  # значение рядом с подписью
     "zone": [0.3, 0.55, 0.7, 0.08],          # x, y, ширина, высота в долях страницы
     "type": "text|date|amount|digits|upper|name",
     "validate": "inn|snils|kpp|bik|ogrn|account|passport",
     "yandex_entity": "number"}               # поле из ответа Yandex Vision
  ]
}
Порядок источников значения: Yandex-сущность → MRZ → зона → подпись → регулярное выражение.
"""
from __future__ import annotations

import base64
import copy
import datetime as dt
import io
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from . import llm
from .banking import check_account, check_inn, check_ogrn, check_snils

# ---------------------------------------------------------------- встроенные шаблоны
BUILTIN_TEMPLATES: List[Dict[str, Any]] = [
    {
        "id": "passport_rf", "name": "Паспорт гражданина РФ (разворот с фото)", "category": "Удостоверения личности",
        "keywords": ["паспорт", "российская федерация", "выдан", "код подразделения", "место рождения", "pnrus"],
        "yandex_model": "passport", "mrz": True,
        "fields": [
            {"name": "series_number", "label": "Серия и номер", "required": True, "validate": "passport",
             "regex": r"(?<!\d)(\d{2}\s?\d{2}\s?\d{6})(?!\d)", "type": "digits", "yandex_entity": "number"},
            {"name": "surname", "label": "Фамилия", "required": True, "anchor": "Фамилия", "type": "name",
             "yandex_entity": "surname"},
            {"name": "name", "label": "Имя", "required": True, "anchor": "Имя", "type": "name",
             "yandex_entity": "name"},
            {"name": "patronymic", "label": "Отчество", "anchor": "Отчество", "type": "name",
             "yandex_entity": "middle_name"},
            {"name": "birth_date", "label": "Дата рождения", "required": True, "type": "date",
             "anchor": "Дата рождения", "yandex_entity": "birth_date"},
            {"name": "gender", "label": "Пол", "regex": r"\b(МУЖ|ЖЕН)\b", "type": "upper", "yandex_entity": "gender"},
            {"name": "birth_place", "label": "Место рождения", "anchor": "Место рождения", "anchor_position": "below",
             "yandex_entity": "birth_place"},
            {"name": "issued_by", "label": "Кем выдан", "anchor": "Паспорт выдан", "anchor_position": "below",
             "yandex_entity": "issued_by"},
            {"name": "issue_date", "label": "Дата выдачи", "type": "date", "anchor": "Дата выдачи",
             "yandex_entity": "issue_date"},
            {"name": "subdivision", "label": "Код подразделения", "regex": r"(\d{3}\s?[-–]\s?\d{3})",
             "yandex_entity": "subdivision"},
        ],
    },
    {
        "id": "snils", "name": "СНИЛС", "category": "Удостоверения личности",
        "keywords": ["страховое свидетельство", "снилс", "пенсионного страхования", "страховой номер"],
        "fields": [
            {"name": "snils", "label": "СНИЛС", "required": True, "validate": "snils", "type": "digits",
             "regex": r"(\d{3}[-\s]?\d{3}[-\s]?\d{3}[\s-]+\d{2})"},
            {"name": "fio", "label": "ФИО", "anchor": "Ф.И.О.", "type": "name"},
            {"name": "birth_date", "label": "Дата рождения", "type": "date", "anchor": "Дата и место рождения"},
        ],
    },
    {
        "id": "driver_license", "name": "Водительское удостоверение РФ (лицевая сторона)",
        "category": "Удостоверения личности",
        "keywords": ["водительское удостоверение", "permis de conduire", "гибдд", "гаи"],
        "yandex_model": "driver-license-front",
        "fields": [
            {"name": "number", "label": "Серия и номер", "required": True, "type": "digits",
             "regex": r"5\.\s*(\d{2}\s?\d{2}\s?\d{6})", "yandex_entity": "number"},
            {"name": "surname", "label": "Фамилия", "regex": r"\b1\.\s*([А-ЯЁ\-]{2,})", "type": "name",
             "yandex_entity": "surname"},
            {"name": "name", "label": "Имя и отчество", "regex": r"\b2\.\s*([А-ЯЁ\- ]{2,})", "type": "name",
             "yandex_entity": "name"},
            {"name": "birth_date", "label": "Дата рождения", "type": "date",
             "regex": r"\b3\.\s*(\d{2}\.\d{2}\.\d{4})", "yandex_entity": "birth_date"},
            {"name": "issue_date", "label": "Дата выдачи", "type": "date",
             "regex": r"4a\)?\s*(\d{2}\.\d{2}\.\d{4})", "yandex_entity": "issue_date"},
            {"name": "expiration_date", "label": "Действительно до", "type": "date",
             "regex": r"4b\)?\s*(\d{2}\.\d{2}\.\d{4})", "yandex_entity": "expiration_date"},
            {"name": "categories", "label": "Категории", "regex": r"\b9\.\s*([ABCDEM1 ]+)", "type": "upper"},
        ],
    },
    {
        "id": "payment_order", "name": "Платёжное поручение (форма 0401060)", "category": "Банковские документы",
        "keywords": ["платежное поручение", "платёжное поручение", "0401060", "получатель", "плательщик",
                     "назначение платежа", "сч. №"],
        "fields": [
            {"name": "number", "label": "Номер", "required": True,
             "regex": r"ПЛАТ[ЕЁ]ЖНОЕ ПОРУЧЕНИЕ\s*№?\s*(\d{1,6})"},
            {"name": "date", "label": "Дата", "type": "date",
             "regex": r"ПОРУЧЕНИЕ\s*№?\s*\d{1,6}\s+(\d{2}\.\d{2}\.\d{4})"},
            {"name": "amount", "label": "Сумма", "required": True, "type": "amount",
             "regex": r"Сумма\s*(?!прописью)[^\d]{0,20}(\d[\d\s]*[-=,.]\d{2})"},
            {"name": "payer_inn", "label": "ИНН плательщика", "validate": "inn",
             "regex": r"ИНН\s*(\d{12}|\d{10})(?!\d)"},
            {"name": "payer_account", "label": "Счёт плательщика", "type": "digits",
             "regex": r"ИНН\s*\d{10,12}.*?[СC][чu]\.?\s*№\s*(\d{20})"},
            {"name": "recipient_inn", "label": "ИНН получателя", "validate": "inn",
             "regex": r"(?:ИНН\s*(?:\d{12}|\d{10}).*?){1}ИНН\s*(\d{12}|\d{10})(?!\d)"},
            {"name": "bik", "label": "БИК банка получателя", "validate": "bik",
             "regex": r"(?:БИК\s*\d{9}.*?)?БИК\s*(\d{9})(?!.*БИК)"},
            {"name": "recipient_account", "label": "Счёт получателя", "type": "digits",
             "regex": r"ИНН\s*\d{10,12}.*?ИНН\s*\d{10,12}\D{0,60}?№\s*(\d{20})"},
            {"name": "purpose", "label": "Назначение платежа", "anchor": "Назначение платежа",
             "anchor_position": "above"},
        ],
    },
    {
        "id": "invoice", "name": "Счёт на оплату", "category": "Бухгалтерские документы",
        "keywords": ["счет на оплату", "счёт на оплату", "итого", "в том числе ндс", "всего к оплате", "поставщик"],
        "fields": [
            {"name": "number", "label": "Номер счёта", "required": True,
             "regex": r"Сч[её]т\s+на\s+оплату\s*№\s*([\w\-/]+)"},
            {"name": "date", "label": "Дата", "type": "date",
             "regex": r"Сч[её]т\s+на\s+оплату\s*№\s*[\w\-/]+\s+от\s+([\d\.]+\s*\w*\s*\d{0,4})"},
            {"name": "supplier_inn", "label": "ИНН поставщика", "validate": "inn",
             "regex": r"ИНН\s*(\d{12}|\d{10})(?!\d)"},
            {"name": "account", "label": "Расчётный счёт", "type": "digits", "regex": r"(?:[СC][чu]\.?\s*№|р/с)\s*(\d{20})"},
            {"name": "bik", "label": "БИК", "validate": "bik", "regex": r"БИК\s*(\d{9})"},
            {"name": "total", "label": "Итого к оплате", "required": True, "type": "amount",
             "regex": r"(?:Всего к оплате|Итого)\s*:?\s*(\d[\d\s]*[,.]\d{2})"},
            {"name": "vat", "label": "НДС", "type": "amount",
             "regex": r"(?:В том числе НДС|НДС)[^\d]{0,15}(\d[\d\s]*[,.]\d{2})"},
        ],
    },
    {
        "id": "egrul_extract", "name": "Выписка из ЕГРЮЛ", "category": "Документы контрагентов",
        "keywords": ["выписка", "единого государственного реестра юридических лиц", "огрн", "егрюл"],
        "fields": [
            {"name": "full_name", "label": "Полное наименование", "anchor": "Полное наименование на русском языке"},
            {"name": "ogrn", "label": "ОГРН", "required": True, "validate": "ogrn", "regex": r"ОГРН\s*(\d{13})"},
            {"name": "inn", "label": "ИНН", "required": True, "validate": "inn", "regex": r"ИНН\s*(\d{10})"},
            {"name": "kpp", "label": "КПП", "validate": "kpp", "regex": r"КПП\s*(\d{9})"},
            {"name": "director", "label": "Руководитель", "anchor": "Фамилия Имя Отчество", "type": "name"},
            {"name": "address", "label": "Адрес", "anchor": "Адрес юридического лица"},
        ],
    },
]


def builtin(template_id: str) -> Optional[Dict[str, Any]]:
    return copy.deepcopy(next((t for t in BUILTIN_TEMPLATES if t["id"] == template_id), None))


# ---------------------------------------------------------------- загрузка изображений
def load_pages(path_or_bytes: Any, max_pages: int = 5) -> Tuple[List[bytes], str]:
    """Возвращает PNG-страницы и текстовый слой (для PDF с текстом)."""
    data = path_or_bytes if isinstance(path_or_bytes, (bytes, bytearray)) else open(
        os.path.expanduser(str(path_or_bytes)), "rb").read()
    if data[:4] != b"%PDF":
        return [bytes(data)], ""
    text = ""
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        text = "\n".join((pg.extract_text() or "") for pg in reader.pages[:max_pages])
    except Exception:
        pass
    pages: List[bytes] = []
    try:
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(data)
        for i in range(min(len(pdf), max_pages)):
            img = pdf[i].render(scale=2.5).to_pil()
            buf = io.BytesIO()
            img.save(buf, "PNG")
            pages.append(buf.getvalue())
    except ImportError:
        if not text.strip():
            raise RuntimeError("Для сканов в PDF нужен пакет pypdfium2: pip install pypdfium2") from None
    return pages, text


# ---------------------------------------------------------------- движки
def recognize(image: bytes, engine: str = "tesseract", lang: str = "rus+eng",
              ai_conn: Optional[Dict[str, Any]] = None, yandex_conn: Optional[Dict[str, Any]] = None,
              yandex_model: str = "page") -> Dict[str, Any]:
    """Распознаёт одну страницу. Результат: {text, words: [{text, x, y, w, h, line}] в долях страницы,
    entities: {} (для моделей Yandex Vision)}."""
    if engine == "tesseract":
        return _tesseract(image, lang)
    if engine == "yandex":
        return yandex_vision(image, yandex_conn or {}, yandex_model, lang)
    if engine == "ai":
        if not ai_conn:
            raise RuntimeError("Для OCR через ИИ нужно подключение к модели")
        text = llm.chat(ai_conn, "Распознай весь текст на изображении документа. Сохрани порядок строк, "
                                 "не добавляй комментариев.", images=[image])
        return {"text": text, "words": [], "entities": {}}
    raise ValueError(f"Неизвестный движок OCR: {engine}")


def _prepare(image: bytes) -> Any:
    from PIL import Image, ImageOps

    img = Image.open(io.BytesIO(image))
    img = ImageOps.exif_transpose(img).convert("L")
    if img.width < 1600:  # мелкий текст распознаётся лучше после увеличения
        k = 1600 / img.width
        img = img.resize((int(img.width * k), int(img.height * k)), Image.LANCZOS)
    return ImageOps.autocontrast(img, cutoff=1)


def _tesseract(image: bytes, lang: str, psm: int = 3) -> Dict[str, Any]:
    try:
        import pytesseract
    except ImportError:
        raise RuntimeError("Нужен пакет pytesseract и программа Tesseract (см. документацию модуля OCR)") from None
    img = _prepare(image)
    try:
        data = pytesseract.image_to_data(img, lang=lang, config=f"--psm {psm}",
                                         output_type=pytesseract.Output.DICT)
    except pytesseract.TesseractNotFoundError:
        raise RuntimeError("Tesseract не установлен: apt install tesseract-ocr tesseract-ocr-rus / "
                           "brew install tesseract tesseract-lang / установщик UB-Mannheim для Windows") from None
    words = []
    for i, txt in enumerate(data["text"]):
        if txt.strip() and float(data["conf"][i]) >= 0:
            words.append({"text": txt, "x": data["left"][i] / img.width, "y": data["top"][i] / img.height,
                          "w": data["width"][i] / img.width, "h": data["height"][i] / img.height,
                          "line": f'{data["block_num"][i]}.{data["par_num"][i]}.{data["line_num"][i]}',
                          "conf": float(data["conf"][i])})
    return {"text": words_to_text(words), "words": words, "entities": {}}


def words_to_text(words: List[Dict[str, Any]]) -> str:
    lines: Dict[str, List[Dict[str, Any]]] = {}
    for w in words:
        lines.setdefault(w["line"], []).append(w)
    ordered = sorted(lines.values(), key=lambda ws: (min(w["y"] for w in ws), min(w["x"] for w in ws)))
    return "\n".join(" ".join(w["text"] for w in sorted(ws, key=lambda w: w["x"])) for ws in ordered)


def yandex_vision(image: bytes, conn: Dict[str, Any], model: str = "page", lang: str = "rus+eng") -> Dict[str, Any]:
    import httpx

    mime = {"image/png": "PNG", "image/jpeg": "JPEG", "application/pdf": "PDF"}.get(llm._mime(image), "PNG")
    codes = [{"rus": "ru", "eng": "en", "kaz": "kk", "ukr": "uk", "bel": "be"}.get(c, c) for c in lang.split("+")]
    body = {"mimeType": mime, "languageCodes": codes, "model": model or "page",
            "content": base64.b64encode(image).decode()}
    base = (conn.get("ocr_url") or "https://ocr.api.cloud.yandex.net").rstrip("/")
    r = httpx.post(f"{base}/ocr/v1/recognizeText", json=body, headers=llm.yandex_headers(conn), timeout=120)
    if r.status_code >= 400:
        raise RuntimeError(f"Yandex Vision OCR {r.status_code}: {r.text[:400]}")
    ann = (r.json().get("result") or {}).get("textAnnotation") or {}
    width, height = float(ann.get("width") or 1), float(ann.get("height") or 1)
    words = []
    for bi, blk in enumerate(ann.get("blocks") or []):
        for li, line in enumerate(blk.get("lines") or []):
            for w in line.get("words") or []:
                vs = (w.get("boundingBox") or {}).get("vertices") or []
                xs = [float(v.get("x", 0)) for v in vs] or [0.0]
                ys = [float(v.get("y", 0)) for v in vs] or [0.0]
                words.append({"text": w.get("text", ""), "x": min(xs) / width, "y": min(ys) / height,
                              "w": (max(xs) - min(xs)) / width, "h": (max(ys) - min(ys)) / height,
                              "line": f"{bi}.{li}", "conf": 100.0})
    entities = {e.get("name"): e.get("text") for e in ann.get("entities") or [] if e.get("name")}
    return {"text": ann.get("fullText") or words_to_text(words), "words": words, "entities": entities}


# ---------------------------------------------------------------- классификация
def classify(text: str, templates: List[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], float]:
    low = _norm(text)
    best, best_score = None, 0.0
    for t in templates:
        kws = [_norm(k) for k in t.get("keywords") or []]
        if not kws:
            continue
        score = sum(1 for k in kws if k in low) / len(kws)
        if score > best_score:
            best, best_score = t, score
    return best, round(best_score, 2)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower().replace("ё", "е"))


# ---------------------------------------------------------------- извлечение полей
def extract(pages: List[Dict[str, Any]], template: Dict[str, Any]) -> Dict[str, Any]:
    """pages — результаты recognize(). Возвращает {fields, sources, errors, missing, ok}."""
    full = "\n".join(p["text"] for p in pages)
    words = pages[0]["words"] if pages else []
    entities: Dict[str, Any] = {}
    for p in pages:
        entities.update(p.get("entities") or {})
    mrz = parse_passport_mrz(full) if template.get("mrz") else None
    fields: Dict[str, Any] = {}
    sources: Dict[str, str] = {}
    errors: Dict[str, str] = {}
    for f in template.get("fields") or []:
        name = f["name"]
        value, source = None, ""
        if f.get("yandex_entity") and entities.get(f["yandex_entity"]):
            value, source = entities[f["yandex_entity"]], "yandex"
        if value is None and mrz and mrz.get(name):
            value, source = mrz[name], "mrz"
        if value is None and f.get("zone") and words:
            zt = zone_text(words, f["zone"])
            if zt:
                value, source = _apply_regex(zt, f.get("regex")) if f.get("regex") else zt, "zone"
        if value is None and f.get("anchor"):
            value = anchor_value(full, f["anchor"], f.get("anchor_position") or "right")
            source = "anchor" if value else ""
        if value is None and f.get("regex"):
            value = _apply_regex(full, f["regex"])
            source = "regex" if value else ""
        if value is not None:
            value = normalize(value, f.get("type") or "text")
        if value not in (None, "") and f.get("validate"):
            err = validate_value(f["validate"], value, fields)
            if err:
                errors[name] = err
        fields[name] = value
        if source:
            sources[name] = source
    missing = [f["name"] for f in template.get("fields") or [] if f.get("required") and not fields.get(f["name"])]
    return {"template": template.get("id"), "fields": fields, "sources": sources, "errors": errors,
            "missing": missing, "ok": not missing and not errors, "mrz_valid": (mrz or {}).get("valid")}


def zone_text(words: List[Dict[str, Any]], zone: List[float]) -> str:
    x, y, w, h = [float(v) for v in zone]
    inside = [wd for wd in words if x <= wd["x"] + wd["w"] / 2 <= x + w and y <= wd["y"] + wd["h"] / 2 <= y + h]
    return words_to_text(inside).strip()


def _apply_regex(text: str, pattern: str) -> Optional[str]:
    m = re.search(pattern, text, re.I | re.S)
    if not m:
        return None
    return (m.group(1) if m.groups() else m.group(0)).strip()


def anchor_value(text: str, anchor: str, position: str = "right") -> Optional[str]:
    lines = [ln.strip() for ln in text.splitlines()]
    na = _norm(anchor)
    for i, ln in enumerate(lines):
        nl = _norm(ln)
        pos = nl.find(na)
        if pos < 0:
            continue
        if position == "right":
            rest = ln[pos + len(anchor):].strip(" :.-–\t")
            if rest:
                return rest
            position = "below"
        if position == "below":
            for nxt in lines[i + 1:i + 4]:
                if nxt.strip():
                    return nxt.strip()
        if position == "above":
            for prev in reversed(lines[max(0, i - 3):i]):
                if prev.strip():
                    return prev.strip()
    return None


MONTHS = {"января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6, "июля": 7, "августа": 8,
          "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12}


def normalize(value: Any, kind: str) -> Any:
    s = str(value).strip()
    if kind == "digits":
        return re.sub(r"\D", "", s)
    if kind == "upper":
        return s.upper()
    if kind == "name":
        s = re.sub(r"[^А-Яа-яЁёA-Za-z\- ]", "", s).strip()
        return " ".join(part.capitalize() for part in s.split())
    if kind == "amount":
        s = s.replace(" ", "").replace(" ", "").replace("-", ".").replace("=", ".").replace(",", ".")
        try:
            return round(float(s), 2)
        except ValueError:
            return None
    if kind == "date":
        m = re.search(r"(\d{1,2})[.\-/ ](\d{1,2})[.\-/ ](\d{2,4})", s)
        if m:
            d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        else:
            m = re.search(r"(\d{1,2})\s+([а-я]+)\s+(\d{4})", s.lower())
            if not m or m.group(2) not in MONTHS:
                return s
            d, mo, y = int(m.group(1)), MONTHS[m.group(2)], int(m.group(3))
        if y < 100:
            y += 2000 if y <= dt.date.today().year % 100 else 1900
        try:
            return dt.date(y, mo, d).strftime("%d.%m.%Y")
        except ValueError:
            return s
    return re.sub(r"\s+", " ", s)


def validate_value(kind: str, value: Any, fields: Dict[str, Any]) -> Optional[str]:
    v = re.sub(r"\D", "", str(value))
    if kind == "inn":
        return None if check_inn(v) else "контрольная сумма ИНН не сходится"
    if kind == "snils":
        return None if check_snils(v) else "контрольная сумма СНИЛС не сходится"
    if kind == "ogrn":
        return None if check_ogrn(v) else "контрольная сумма ОГРН не сходится"
    if kind == "kpp":
        return None if re.fullmatch(r"\d{4}[\dA-Z]{2}\d{3}", str(value)) else "КПП должен состоять из 9 символов"
    if kind == "bik":
        return None if re.fullmatch(r"04\d{7}", v) else "БИК должен начинаться с 04 и состоять из 9 цифр"
    if kind == "passport":
        return None if len(v) == 10 else "серия и номер паспорта — 10 цифр"
    if kind == "account":
        bik = re.sub(r"\D", "", str(fields.get("bik") or ""))
        if bik and not check_account(v, bik):
            return "контрольный ключ счёта не сходится с БИК"
    return None


# ---------------------------------------------------------------- MRZ паспорта РФ
_MRZ_RU = dict(zip("ABVGDE2JZIQKLMNOPRSTUFHC34WXY9678", "АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ"))


def _mrz_check(data: str) -> int:
    weights = (7, 3, 1)
    total = 0
    for i, ch in enumerate(data):
        if ch.isdigit():
            v = int(ch)
        elif ch.isalpha():
            v = ord(ch.upper()) - 55
        else:
            v = 0
        total += v * weights[i % 3]
    return total % 10


def parse_passport_mrz(text: str) -> Optional[Dict[str, Any]]:
    """Разбор двух строк MRZ внутреннего паспорта РФ (PNRUS…). Кириллица
    восстанавливается по стандартной для паспорта РФ схеме транслитерации."""
    clean = [re.sub(r"[^A-Z0-9<]", "", ln.upper().replace(" ", "").replace("«", "<").replace("K<", "K<"))
             for ln in text.splitlines()]
    for i, ln in enumerate(clean):
        if ln.startswith("PNRUS") and i + 1 < len(clean) and len(clean[i + 1]) >= 40:
            l1, l2 = ln.ljust(44, "<")[:44], clean[i + 1].ljust(44, "<")[:44]
            names = l1[5:].split("<<")
            surname = _mrz_ru(names[0])
            given = [_mrz_ru(n) for n in (names[1].split("<") if len(names) > 1 else []) if n]
            number9, birth, sex, opt = l2[0:9], l2[13:19], l2[20], l2[28:42]
            series_number = number9[:3] + opt[0] + number9[3:]
            valid = (_mrz_check(l2[0:9]) == _digit(l2[9]) and _mrz_check(l2[13:19]) == _digit(l2[19]))
            issue = opt[1:7]
            res = {"surname": surname.capitalize(), "name": (given[0] if given else "").capitalize(),
                   "patronymic": (given[1] if len(given) > 1 else "").capitalize(),
                   "series_number": series_number, "birth_date": _mrz_date(birth, past=True),
                   "gender": {"M": "МУЖ", "F": "ЖЕН"}.get(sex, ""), "issue_date": _mrz_date(issue, past=True),
                   "subdivision": f"{opt[7:10]}-{opt[10:13]}" if opt[7:13].isdigit() else "", "valid": valid}
            return res
    return None


def _digit(ch: str) -> int:
    return int(ch) if ch.isdigit() else -1


def _mrz_ru(s: str) -> str:
    return "".join(_MRZ_RU.get(c, c) for c in s.replace("<", " ").strip())


def _mrz_date(yymmdd: str, past: bool = True) -> str:
    if not yymmdd.isdigit():
        return ""
    yy, mm, dd = int(yymmdd[:2]), int(yymmdd[2:4]), int(yymmdd[4:6])
    year = 2000 + yy if 2000 + yy <= dt.date.today().year else 1900 + yy
    try:
        return dt.date(year, mm, dd).strftime("%d.%m.%Y")
    except ValueError:
        return ""


# ---------------------------------------------------------------- извлечение через ИИ
def extract_with_ai(conn: Dict[str, Any], template: Dict[str, Any], text: str = "",
                    images: Optional[List[bytes]] = None) -> Dict[str, Any]:
    fields = template.get("fields") or []
    spec = "\n".join(f'- "{f["name"]}": {f.get("label", f["name"])}' for f in fields)
    prompt = (f"Документ: {template.get('name', 'документ')}.\n"
              f"Извлеки поля и верни только JSON-объект с ключами:\n{spec}\n"
              "Если поля нет — null. Даты в формате ДД.ММ.ГГГГ, суммы — числом.")
    if text:
        prompt += "\n\nТекст документа:\n" + text[:60000]
    data = llm.extract_json(llm.chat(conn, prompt, images=images or []))
    result_fields, errors = {}, {}
    for f in fields:
        val = data.get(f["name"]) if isinstance(data, dict) else None
        if val is not None and f.get("type"):
            val = normalize(val, f["type"])
        if val not in (None, "") and f.get("validate"):
            err = validate_value(f["validate"], val, result_fields)
            if err:
                errors[f["name"]] = err
        result_fields[f["name"]] = val
    missing = [f["name"] for f in fields if f.get("required") and not result_fields.get(f["name"])]
    return {"template": template.get("id"), "fields": result_fields, "sources": {k: "ai" for k in result_fields},
            "errors": errors, "missing": missing, "ok": not missing and not errors}

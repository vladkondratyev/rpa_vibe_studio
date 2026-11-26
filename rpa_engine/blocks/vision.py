"""Компьютерное зрение: работа с интерфейсами без доступных элементов —
Citrix, RDP, VDI, терминальные и устаревшие приложения.

Робот смотрит на экран как человек: ищет фрагмент-образец (кнопку, иконку,
подпись) или текст через OCR и кликает по найденному месту.

Зависимости (ставятся отдельно, extra «vision»):
    pip install opencv-python-headless numpy pillow pyautogui pytesseract
OCR: установленный Tesseract с русским языком
    (Windows: UB-Mannheim installer; macOS: brew install tesseract tesseract-lang;
     Linux: apt install tesseract-ocr tesseract-ocr-rus).

Область — «x,y,ширина,высота» в логических координатах экрана; пусто = весь экран.
"""
from __future__ import annotations

import io
import os
import platform
import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple

from ..context import Context
from ..registry import Output, Param, block

P = Dict[str, Any]
CAT = "Vision · Citrix"

REGION = Param("region", "Область экрана", help="x,y,ширина,высота; пусто — весь экран")
IMAGE = Param("image", "Образец (файл PNG)", required=True,
              help="Вырезанный скриншот кнопки или поля. Снимите его блоком «Скриншот экрана»")
CONFIDENCE = Param("confidence", "Точность совпадения", "number", default=0.85, help="0..1")
TIMEOUT = Param("timeout", "Ждать, сек", "number", default=10)
LANG = Param("lang", "Языки OCR", default="rus+eng")


def _need(module: str, pip: str) -> Any:
    try:
        return __import__(module)
    except ImportError:
        raise RuntimeError(f"Нужен пакет {pip}: pip install {pip}") from None


def parse_region(region: Any) -> Optional[Tuple[int, int, int, int]]:
    if region in (None, ""):
        return None
    if isinstance(region, (list, tuple)):
        vals = [int(float(v)) for v in region]
    else:
        vals = [int(float(v)) for v in str(region).replace(";", ",").split(",")]
    if len(vals) != 4:
        raise ValueError("Область задаётся четырьмя числами: x,y,ширина,высота")
    return vals[0], vals[1], vals[2], vals[3]


# ---------------------------------------------------------------- экран
def grab(region: Optional[Tuple[int, int, int, int]] = None) -> Tuple[Any, float]:
    """Снимок экрана (PIL.Image) и масштаб пикселей к логическим координатам
    (на Retina/HiDPI снимок в 2 раза больше координат мыши)."""
    _need("PIL", "pillow")
    from PIL import Image, ImageGrab

    if platform.system() == "Darwin":
        # ImageGrab на macOS тоже вызывает screencapture; делаем явно и без звука
        path = os.path.join(os.environ.get("TMPDIR", "/tmp"), f"rpa-screen-{os.getpid()}.png")
        subprocess.run(["screencapture", "-x", "-C", path], check=True)
        img = Image.open(path)
        img.load()
        os.unlink(path)
    else:
        img = ImageGrab.grab(all_screens=True)
    scale = 1.0
    try:
        import pyautogui

        w, _ = pyautogui.size()
        scale = img.width / float(w) if w else 1.0
    except Exception:
        pass
    if region:
        x, y, w, h = region
        img = img.crop((int(x * scale), int(y * scale), int((x + w) * scale), int((y + h) * scale)))
    return img.convert("RGB"), scale


def match_template(haystack: Any, needle: Any, confidence: float = 0.85,
                   multi: bool = False) -> List[Tuple[int, int, int, int, float]]:
    """Ищет needle в haystack (PIL-изображения). Возвращает (x, y, w, h, score)
    в пикселях haystack, лучшие первыми."""
    cv2 = _need("cv2", "opencv-python-headless")
    np = _need("numpy", "numpy")
    hay = cv2.cvtColor(np.array(haystack.convert("RGB")), cv2.COLOR_RGB2GRAY)
    ndl = cv2.cvtColor(np.array(needle.convert("RGB")), cv2.COLOR_RGB2GRAY)
    nh, nw = ndl.shape[:2]
    if nh > hay.shape[0] or nw > hay.shape[1]:
        return []
    res = cv2.matchTemplate(hay, ndl, cv2.TM_CCOEFF_NORMED)
    if not multi:
        _, score, _, loc = cv2.minMaxLoc(res)
        return [(loc[0], loc[1], nw, nh, float(score))] if score >= confidence else []
    ys, xs = np.where(res >= confidence)
    found: List[Tuple[int, int, int, int, float]] = []
    for x, y in sorted(zip(xs.tolist(), ys.tolist()), key=lambda t: -res[t[1], t[0]]):
        if all(abs(x - fx) > nw // 2 or abs(y - fy) > nh // 2 for fx, fy, *_ in found):
            found.append((x, y, nw, nh, float(res[y, x])))
    return found


def _load_needle(path: str, scale: float) -> Any:
    from PIL import Image

    img = Image.open(os.path.expanduser(path)).convert("RGB")
    return img


def locate(image: str, region: Any = None, confidence: float = 0.85) -> Optional[Dict[str, Any]]:
    reg = parse_region(region)
    screen, scale = grab(reg)
    needle = _load_needle(image, scale)
    hits = match_template(screen, needle, confidence)
    if not hits and abs(scale - 1.0) > 0.01:  # образец снят на экране с другим масштабом
        needle = needle.resize((max(1, int(needle.width * scale)), max(1, int(needle.height * scale))))
        hits = match_template(screen, needle, confidence)
        if not hits:
            needle2 = needle.resize((max(1, int(needle.width / scale ** 2)), max(1, int(needle.height / scale ** 2))))
            hits = match_template(screen, needle2, confidence)
    if not hits:
        return None
    x, y, w, h, score = hits[0]
    ox, oy = (reg[0], reg[1]) if reg else (0, 0)
    lx, ly, lw, lh = ox + x / scale, oy + y / scale, w / scale, h / scale
    return {"x": round(lx), "y": round(ly), "width": round(lw), "height": round(lh),
            "center_x": round(lx + lw / 2), "center_y": round(ly + lh / 2), "score": round(score, 3)}


def ocr_words(region: Any = None, lang: str = "rus+eng") -> Tuple[List[Dict[str, Any]], str]:
    pytesseract = _need("pytesseract", "pytesseract")
    reg = parse_region(region)
    img, scale = grab(reg)
    ox, oy = (reg[0], reg[1]) if reg else (0, 0)
    # мелкий шрифт в Citrix распознаётся лучше после увеличения
    if img.width < 2000:
        factor = 2
        img = img.resize((img.width * factor, img.height * factor))
    else:
        factor = 1
    try:
        data = pytesseract.image_to_data(img, lang=lang, output_type=pytesseract.Output.DICT)
        text = pytesseract.image_to_string(img, lang=lang)
    except pytesseract.TesseractNotFoundError:
        raise RuntimeError("Tesseract не установлен: см. описание модуля Vision") from None
    words = []
    for i, word in enumerate(data["text"]):
        if not word.strip() or float(data["conf"][i]) < 0:
            continue
        k = factor * scale
        words.append({"text": word, "x": ox + data["left"][i] / k, "y": oy + data["top"][i] / k,
                      "width": data["width"][i] / k, "height": data["height"][i] / k,
                      "line": (data["block_num"][i], data["par_num"][i], data["line_num"][i]),
                      "conf": float(data["conf"][i])})
    return words, text


def find_text(words: List[Dict[str, Any]], phrase: str, occurrence: int = 0) -> Optional[Dict[str, Any]]:
    """Ищет фразу (можно из нескольких слов) в результатах OCR без учёта регистра."""
    target = phrase.lower().split()
    if not target:
        return None
    hits = []
    for i in range(len(words)):
        chunk = words[i:i + len(target)]
        if len(chunk) < len(target) or len({w["line"] for w in chunk}) > 1:
            continue
        ok = all(t in w["text"].lower() for t, w in zip(target, chunk))
        if ok:
            x0, y0 = chunk[0]["x"], min(w["y"] for w in chunk)
            x1 = chunk[-1]["x"] + chunk[-1]["width"]
            y1 = max(w["y"] + w["height"] for w in chunk)
            hits.append({"x": round(x0), "y": round(y0), "width": round(x1 - x0), "height": round(y1 - y0),
                         "center_x": round((x0 + x1) / 2), "center_y": round((y0 + y1) / 2)})
    return hits[occurrence] if len(hits) > occurrence else None


def mouse_click(x: float, y: float, button: str = "left", clicks: int = 1) -> None:
    pyautogui = _need("pyautogui", "pyautogui")
    pyautogui.FAILSAFE = False
    pyautogui.click(x=x, y=y, button=button or "left", clicks=clicks, interval=0.08)


def type_text(text: str, method: str = "keys") -> None:
    pyautogui = _need("pyautogui", "pyautogui")
    if method == "clipboard" or any(ord(c) > 127 for c in text):
        # кириллицу и спецсимволы надёжнее вставлять через буфер обмена:
        # в Citrix он обычно проброшен в сессию
        import pyperclip

        pyperclip.copy(text)
        pyautogui.hotkey("command" if platform.system() == "Darwin" else "ctrl", "v")
    else:
        pyautogui.write(text, interval=0.02)


def _wait(ctx: Context, timeout: float, probe: Any) -> Any:
    deadline = time.monotonic() + timeout
    while True:
        ctx.check_cancel()
        res = probe()
        if res is not None or time.monotonic() >= deadline:
            return res
        time.sleep(0.4)


def _shot_on_error(ctx: Context) -> None:
    def hook() -> None:
        try:
            img, _ = grab()
            buf = io.BytesIO()
            img.save(buf, "PNG")
            ctx.add_artifact("screen_error.png", buf.getvalue())
        except Exception:
            pass

    hooks = ctx.resources.setdefault("_error_hooks", [])
    if not any(getattr(h, "_vision", False) for h in hooks):
        hook._vision = True  # type: ignore[attr-defined]
        hooks.append(hook)


# ---------------------------------------------------------------- блоки
@block(
    "vision.find_image", "Найти на экране", CAT,
    params=[IMAGE, REGION, CONFIDENCE, TIMEOUT,
            Param("result", "Записать в", "variable", default="found")],
    outputs=[Output("found", "Найдено"), Output("not_found", "Нет")],
    description="Ищет образец на экране. В переменную — координаты {x, y, center_x, center_y, score}.",
)
def find_image(ctx: Context, p: P) -> str:
    _shot_on_error(ctx)
    hit = _wait(ctx, float(p["timeout"] or 0), lambda: locate(p["image"], p["region"], float(p["confidence"] or 0.85)))
    if p["result"]:
        ctx.vars[p["result"]] = hit
    return "found" if hit else "not_found"


@block(
    "vision.click_image", "Клик по картинке", CAT,
    params=[IMAGE, REGION, CONFIDENCE, TIMEOUT,
            Param("button", "Кнопка мыши", "select", default="left", options=["left", "right", "middle"]),
            Param("clicks", "Кликов", "number", default=1),
            Param("offset", "Смещение x,y", help="От центра образца, например 120,0 — поле правее подписи")],
)
def click_image(ctx: Context, p: P) -> None:
    _shot_on_error(ctx)
    hit = _wait(ctx, float(p["timeout"] or 10), lambda: locate(p["image"], p["region"], float(p["confidence"] or 0.85)))
    if not hit:
        raise RuntimeError(f"Образец {os.path.basename(p['image'])} не найден на экране")
    dx, dy = (parse_offset(p["offset"]))
    mouse_click(hit["center_x"] + dx, hit["center_y"] + dy, p["button"], int(p["clicks"] or 1))
    ctx.log(f"Клик по {os.path.basename(p['image'])} ({hit['center_x'] + dx}, {hit['center_y'] + dy}), "
            f"совпадение {hit['score']}")


def parse_offset(raw: Any) -> Tuple[float, float]:
    if not raw:
        return 0.0, 0.0
    parts = [float(v) for v in str(raw).replace(";", ",").split(",")]
    return (parts + [0.0, 0.0])[0], (parts + [0.0, 0.0])[1]


@block(
    "vision.wait_image", "Ждать картинку", CAT,
    params=[IMAGE, REGION, CONFIDENCE, Param("timeout", "Ждать, сек", "number", default=30),
            Param("disappear", "Ждать исчезновения", "boolean", default=False)],
)
def wait_image(ctx: Context, p: P) -> None:
    _shot_on_error(ctx)
    conf = float(p["confidence"] or 0.85)
    if p["disappear"]:
        gone = _wait(ctx, float(p["timeout"] or 30),
                     lambda: True if locate(p["image"], p["region"], conf) is None else None)
        if not gone:
            raise RuntimeError("Образец не исчез с экрана")
        return
    if not _wait(ctx, float(p["timeout"] or 30), lambda: locate(p["image"], p["region"], conf)):
        raise RuntimeError(f"Образец {os.path.basename(p['image'])} не появился")


@block(
    "vision.ocr", "Распознать текст (OCR)", CAT,
    params=[REGION, LANG, Param("result", "Записать в", "variable", required=True, default="text")],
)
def ocr(ctx: Context, p: P) -> None:
    _, text = ocr_words(p["region"], p["lang"] or "rus+eng")
    ctx.vars[p["result"]] = text.strip()
    ctx.log(f"Распознано символов: {len(text.strip())}")


@block(
    "vision.click_text", "Клик по тексту", CAT,
    params=[Param("text", "Текст на экране", required=True), REGION, LANG, TIMEOUT,
            Param("occurrence", "Какое вхождение (с 0)", "number", default=0),
            Param("offset", "Смещение x,y"),
            Param("button", "Кнопка мыши", "select", default="left", options=["left", "right", "middle"]),
            Param("clicks", "Кликов", "number", default=1)],
    description="Находит надпись через OCR и кликает по ней (или со смещением — в поле рядом с подписью).",
)
def click_text(ctx: Context, p: P) -> None:
    _shot_on_error(ctx)
    lang = p["lang"] or "rus+eng"
    hit = _wait(ctx, float(p["timeout"] or 10),
                lambda: find_text(ocr_words(p["region"], lang)[0], p["text"], int(p["occurrence"] or 0)))
    if not hit:
        raise RuntimeError(f"Текст «{p['text']}» не найден на экране")
    dx, dy = parse_offset(p["offset"])
    mouse_click(hit["center_x"] + dx, hit["center_y"] + dy, p["button"], int(p["clicks"] or 1))


@block(
    "vision.wait_text", "Ждать текст", CAT,
    params=[Param("text", "Текст на экране", required=True), REGION, LANG,
            Param("timeout", "Ждать, сек", "number", default=30)],
    outputs=[Output("found", "Появился"), Output("not_found", "Нет")],
)
def wait_text(ctx: Context, p: P) -> str:
    lang = p["lang"] or "rus+eng"
    hit = _wait(ctx, float(p["timeout"] or 30), lambda: find_text(ocr_words(p["region"], lang)[0], p["text"]))
    return "found" if hit else "not_found"


@block(
    "vision.click", "Клик по координатам", CAT,
    params=[Param("x", "X", "number", required=True), Param("y", "Y", "number", required=True),
            Param("button", "Кнопка мыши", "select", default="left", options=["left", "right", "middle"]),
            Param("clicks", "Кликов", "number", default=1)],
)
def click_xy(ctx: Context, p: P) -> None:
    mouse_click(float(p["x"]), float(p["y"]), p["button"], int(p["clicks"] or 1))


@block(
    "vision.type", "Ввод в сессию", CAT,
    params=[Param("text", "Текст"), Param("hotkey", "Сочетание клавиш", help="ctrl+s, enter, tab"),
            Param("method", "Способ ввода", "select", default="keys", options=["keys", "clipboard"],
                  help="Кириллица всегда вставляется через буфер обмена")],
    description="Печатает текст и нажимает клавиши — для Citrix/RDP, где нет доступа к элементам окна.",
)
def type_(ctx: Context, p: P) -> None:
    if p["text"]:
        type_text(str(p["text"]), p["method"] or "keys")
    if p["hotkey"]:
        pyautogui = _need("pyautogui", "pyautogui")
        pyautogui.hotkey(*[k.strip().lower().replace("cmd", "command") for k in p["hotkey"].split("+")])


@block(
    "vision.screenshot", "Скриншот экрана", CAT,
    params=[REGION, Param("path", "Сохранить в файл", help="Пусто — только в артефакты запуска"),
            Param("name", "Имя артефакта", default="screen.png")],
    description="Снимок экрана или области — для отчёта и для подготовки образцов.",
)
def screenshot(ctx: Context, p: P) -> None:
    img, _ = grab(parse_region(p["region"]))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    data = buf.getvalue()
    if p["path"]:
        path = os.path.expanduser(p["path"])
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
    ctx.add_artifact(p["name"] or "screen.png", data)

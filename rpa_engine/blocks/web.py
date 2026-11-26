"""Блоки веб-автоматизации на Playwright.

Селекторы — синтаксис Playwright: CSS (`#login`), XPath (`xpath=//button`),
по тексту (`text=Войти`), по роли (`role=button[name="Войти"]`).
"""
from __future__ import annotations

import os
from typing import Any, Dict

from ..context import Context
from ..registry import Output, Param, block

P = Dict[str, Any]
CAT = "Web"

SELECTOR = Param("selector", "Селектор", required=True,
                 help="CSS, xpath=..., text=..., role=button[name=\"...\"]")
TIMEOUT = Param("timeout", "Таймаут, сек", "number", default=30)


def _web(ctx: Context) -> Dict[str, Any]:
    web = ctx.resources.get("web")
    if not web:
        raise RuntimeError("Браузер не открыт: добавьте блок «Открыть браузер»")
    return web


def _page(ctx: Context) -> Any:
    return _web(ctx)["page"]


def _ms(p: P) -> float:
    return float(p.get("timeout") or 30) * 1000


@block(
    "web.open", "Открыть браузер", CAT,
    params=[
        Param("browser", "Браузер", "select", default="chromium",
              options=["chromium", "chrome", "msedge", "firefox", "webkit"]),
        Param("headless", "Без окна (headless)", "boolean", default=False),
        Param("url", "Адрес"),
    ],
)
def open_browser(ctx: Context, p: P) -> None:
    from playwright.sync_api import sync_playwright

    if ctx.resources.get("web"):
        raise RuntimeError("Браузер уже открыт")
    pw = sync_playwright().start()
    kind = p["browser"] or "chromium"
    launch: Dict[str, Any] = {"headless": bool(p["headless"])}
    if kind in ("chrome", "msedge"):
        launcher, launch["channel"] = pw.chromium, kind
    else:
        launcher = getattr(pw, kind)
    try:
        browser = launcher.launch(**launch)
    except Exception:
        pw.stop()
        raise
    context = browser.new_context(accept_downloads=True)
    page = context.new_page()
    entry = {"pw": pw, "browser": browser, "context": context, "page": page}
    ctx.resources["web"] = entry

    def close() -> None:
        if ctx.resources.get("web") is not entry:
            return  # уже закрыт блоком «Закрыть браузер»
        ctx.resources.pop("web", None)
        try:
            browser.close()
        finally:
            pw.stop()

    def screenshot_on_error() -> None:
        web = ctx.resources.get("web")
        if web:
            ctx.add_artifact("error.png", web["page"].screenshot(full_page=True))

    ctx.on_cleanup(close)
    ctx.resources.setdefault("_error_hooks", []).append(screenshot_on_error)
    if p.get("url"):
        page.goto(p["url"])


@block("web.goto", "Перейти по адресу", CAT, params=[Param("url", "Адрес", required=True), TIMEOUT])
def goto(ctx: Context, p: P) -> None:
    _page(ctx).goto(p["url"], timeout=_ms(p))


@block("web.click", "Клик", CAT,
       params=[SELECTOR, Param("double", "Двойной клик", "boolean", default=False), TIMEOUT])
def click(ctx: Context, p: P) -> None:
    loc = _page(ctx).locator(p["selector"]).first
    if p["double"]:
        loc.dblclick(timeout=_ms(p))
    else:
        loc.click(timeout=_ms(p))


@block("web.fill", "Ввести текст", CAT,
       params=[SELECTOR, Param("value", "Текст"), TIMEOUT],
       description="Очищает поле и вводит текст.")
def fill(ctx: Context, p: P) -> None:
    _page(ctx).locator(p["selector"]).first.fill("" if p["value"] is None else str(p["value"]), timeout=_ms(p))


@block("web.press", "Нажать клавишу", CAT,
       params=[Param("key", "Клавиша", required=True, default="Enter", help="Enter, Tab, Control+A ..."),
               Param("selector", "Селектор", help="Пусто = активный элемент"), TIMEOUT])
def press(ctx: Context, p: P) -> None:
    page = _page(ctx)
    if p.get("selector"):
        page.locator(p["selector"]).first.press(p["key"], timeout=_ms(p))
    else:
        page.keyboard.press(p["key"])


@block("web.select", "Выбрать в списке", CAT,
       params=[SELECTOR, Param("value", "Значение или текст", required=True), TIMEOUT])
def select(ctx: Context, p: P) -> None:
    loc = _page(ctx).locator(p["selector"]).first
    try:
        loc.select_option(value=str(p["value"]), timeout=_ms(p))
    except Exception:
        loc.select_option(label=str(p["value"]), timeout=_ms(p))


@block("web.get_text", "Получить текст", CAT,
       params=[SELECTOR, Param("result", "Записать в", "variable", required=True, default="text"), TIMEOUT])
def get_text(ctx: Context, p: P) -> None:
    ctx.vars[p["result"]] = _page(ctx).locator(p["selector"]).first.inner_text(timeout=_ms(p))


@block("web.get_value", "Получить значение поля", CAT,
       params=[SELECTOR, Param("result", "Записать в", "variable", required=True, default="value"), TIMEOUT])
def get_value(ctx: Context, p: P) -> None:
    ctx.vars[p["result"]] = _page(ctx).locator(p["selector"]).first.input_value(timeout=_ms(p))


@block("web.get_attribute", "Получить атрибут", CAT,
       params=[SELECTOR, Param("attribute", "Атрибут", required=True, default="href"),
               Param("result", "Записать в", "variable", required=True, default="value"), TIMEOUT])
def get_attribute(ctx: Context, p: P) -> None:
    ctx.vars[p["result"]] = _page(ctx).locator(p["selector"]).first.get_attribute(p["attribute"], timeout=_ms(p))


@block("web.wait", "Ждать элемент", CAT,
       params=[SELECTOR, Param("state", "Состояние", "select", default="visible",
                               options=["visible", "hidden", "attached", "detached"]), TIMEOUT])
def wait(ctx: Context, p: P) -> None:
    _page(ctx).locator(p["selector"]).first.wait_for(state=p["state"] or "visible", timeout=_ms(p))


@block("web.exists", "Элемент есть?", CAT,
       params=[SELECTOR, Param("timeout", "Ждать, сек", "number", default=3)],
       outputs=[Output("true", "Да"), Output("false", "Нет")])
def exists(ctx: Context, p: P) -> str:
    try:
        _page(ctx).locator(p["selector"]).first.wait_for(state="visible", timeout=_ms(p))
        return "true"
    except Exception as exc:
        if "Timeout" in type(exc).__name__ or "Timeout" in str(exc):
            return "false"
        raise


@block("web.extract_table", "Извлечь таблицу", CAT,
       params=[Param("selector", "Селектор таблицы", required=True, default="table"),
               Param("result", "Записать в", "variable", required=True, default="rows"), TIMEOUT],
       description="HTML-таблица в список словарей; ключи — заголовки th (или первая строка).")
def extract_table(ctx: Context, p: P) -> None:
    loc = _page(ctx).locator(p["selector"]).first
    loc.wait_for(timeout=_ms(p))
    ctx.vars[p["result"]] = loc.evaluate(
        """t => {
            const rows = Array.from(t.querySelectorAll('tr'));
            if (!rows.length) return [];
            let head = Array.from(rows[0].querySelectorAll('th'));
            let start = 1;
            if (!head.length) head = Array.from(rows[0].querySelectorAll('td'));
            const keys = head.map((c, i) => c.innerText.trim() || ('col' + (i + 1)));
            return rows.slice(start).map(r => {
                const cells = Array.from(r.querySelectorAll('td,th'));
                const o = {};
                keys.forEach((k, i) => { o[k] = cells[i] ? cells[i].innerText.trim() : null; });
                return o;
            }).filter(o => Object.values(o).some(v => v !== null && v !== ''));
        }"""
    )


@block("web.evaluate", "Выполнить JavaScript", CAT,
       params=[Param("script", "Скрипт", "text", required=True, default="() => document.title"),
               Param("result", "Записать в", "variable", default="value")])
def evaluate(ctx: Context, p: P) -> None:
    value = _page(ctx).evaluate(p["script"])
    if p.get("result"):
        ctx.vars[p["result"]] = value


@block("web.download", "Скачать файл", CAT,
       params=[Param("selector", "Кнопка/ссылка скачивания", required=True),
               Param("folder", "Папка", required=True),
               Param("result", "Путь к файлу в", "variable", default="downloaded"), TIMEOUT])
def download(ctx: Context, p: P) -> None:
    page = _page(ctx)
    with page.expect_download(timeout=_ms(p)) as info:
        page.locator(p["selector"]).first.click(timeout=_ms(p))
    d = info.value
    folder = os.path.expanduser(p["folder"])
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, d.suggested_filename)
    d.save_as(path)
    if p.get("result"):
        ctx.vars[p["result"]] = path


@block("web.upload", "Загрузить файл", CAT,
       params=[Param("selector", "Поле input[type=file]", required=True),
               Param("files", "Файл(ы)", required=True, help="Путь или {{ список путей }}"), TIMEOUT])
def upload(ctx: Context, p: P) -> None:
    _page(ctx).locator(p["selector"]).first.set_input_files(p["files"], timeout=_ms(p))


@block("web.screenshot", "Скриншот", CAT,
       params=[Param("path", "Файл", help="Пусто = приложить к журналу запуска")])
def screenshot(ctx: Context, p: P) -> None:
    page = _page(ctx)
    if p.get("path"):
        page.screenshot(path=os.path.expanduser(p["path"]), full_page=True)
    else:
        ctx.add_artifact("screenshot.png", page.screenshot(full_page=True))


@block("web.close", "Закрыть браузер", CAT)
def close(ctx: Context, p: P) -> None:
    web = ctx.resources.pop("web", None)
    if web:
        try:
            web["browser"].close()
        finally:
            web["pw"].stop()

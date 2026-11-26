"""Рекордер действий в браузере: пользователь проходит сценарий, а клики,
ввод и выбор в списках превращаются в блоки процесса.

  python -m rpa_robot record https://portal.example.com --out process.json
  python -m rpa_robot record https://portal.example.com --server http://orch:8000 --token … --name "Вход в портал"
"""
from __future__ import annotations

import json
import threading
from typing import Any, Dict, List, Optional

# Скрипт в странице: устойчивый селектор и события click / change / Enter.
RECORDER_JS = r"""
(() => {
  if (window.__rpaRecorder) return; window.__rpaRecorder = true;
  const esc = (s) => s.replace(/"/g, '\\"');
  const sel = (el) => {
    if (!el || el.nodeType !== 1) return null;
    for (const a of ['data-testid', 'data-test', 'data-qa']) if (el.getAttribute(a)) return `[${a}="${esc(el.getAttribute(a))}"]`;
    if (el.id && !/\d{4,}/.test(el.id)) return '#' + CSS.escape(el.id);
    if (el.name && ['INPUT', 'SELECT', 'TEXTAREA'].includes(el.tagName)) return `${el.tagName.toLowerCase()}[name="${esc(el.name)}"]`;
    const role = el.getAttribute('role') || ({A: 'link', BUTTON: 'button'})[el.tagName];
    const text = (el.innerText || el.value || '').trim().replace(/\s+/g, ' ');
    if (role && text && text.length <= 40) return `role=${role}[name="${esc(text)}"]`;
    if (el.getAttribute('aria-label')) return `[aria-label="${esc(el.getAttribute('aria-label'))}"]`;
    if (el.placeholder) return `[placeholder="${esc(el.placeholder)}"]`;
    if (text && text.length <= 30 && el.children.length === 0) return `text=${text}`;
    const path = [];
    for (let e = el; e && e.nodeType === 1 && path.length < 5; e = e.parentElement) {
      let part = e.tagName.toLowerCase();
      if (e.id && !/\d{4,}/.test(e.id)) { path.unshift('#' + CSS.escape(e.id)); break; }
      const sib = e.parentElement ? [...e.parentElement.children].filter(c => c.tagName === e.tagName) : [];
      if (sib.length > 1) part += `:nth-of-type(${sib.indexOf(e) + 1})`;
      path.unshift(part);
    }
    return path.join(' > ');
  };
  const target = (e) => e.target.closest('button, a, input, select, textarea, [role], label, li, td, span, div') || e.target;
  document.addEventListener('click', (e) => {
    const el = target(e);
    if (['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName) && !['checkbox', 'radio', 'submit', 'button'].includes(el.type)) return;
    window.__rpaRecord({type: 'click', selector: sel(el), text: (el.innerText || '').trim().slice(0, 60)});
  }, true);
  document.addEventListener('change', (e) => {
    const el = e.target;
    if (el.tagName === 'SELECT') window.__rpaRecord({type: 'select', selector: sel(el), value: el.value});
    else if (['checkbox', 'radio'].includes(el.type)) return;
    else if (el.type === 'file') window.__rpaRecord({type: 'upload', selector: sel(el)});
    else window.__rpaRecord({type: 'fill', selector: sel(el), value: el.value, secret: el.type === 'password'});
  }, true);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && ['INPUT'].includes(e.target.tagName)) {
      const el = e.target;
      window.__rpaRecord({type: 'fill', selector: sel(el), value: el.value, secret: el.type === 'password'});
      window.__rpaRecord({type: 'press', selector: sel(el), key: 'Enter'});
    }
  }, true);
})();
"""


class Recorder:
    def __init__(self) -> None:
        self.events: List[Dict[str, Any]] = []
        self.lock = threading.Lock()

    def on_event(self, source: Any, event: Dict[str, Any]) -> None:
        with self.lock:
            last = self.events[-1] if self.events else None
            if event["type"] == "fill" and last and last["type"] == "fill" and last["selector"] == event["selector"]:
                self.events[-1] = event  # несколько изменений одного поля — одно действие
            else:
                self.events.append(event)
        print("  ●", event["type"], event.get("selector"), event.get("value", "") if not event.get("secret") else "***")

    def on_navigate(self, url: str) -> None:
        with self.lock:
            if self.events and self.events[-1]["type"] in ("click", "press"):
                return  # переход — следствие клика, отдельный шаг не нужен
            self.events.append({"type": "goto", "url": url})


def record(url: str, headless: bool = False, until: Any = None, browser: str = "chromium") -> List[Dict[str, Any]]:
    """Открывает браузер и записывает действия, пока окно не закроют (или until(page) не вернёт True)."""
    from playwright.sync_api import sync_playwright

    rec = Recorder()
    with sync_playwright() as pw:
        b = getattr(pw, browser).launch(headless=headless)
        context = b.new_context()
        context.expose_binding("__rpaRecord", rec.on_event)
        context.add_init_script(RECORDER_JS)
        page = context.new_page()
        page.goto(url)
        print(f"Запись идёт. Пройдите сценарий в браузере и закройте окно, чтобы завершить.")
        if until is not None:
            until(page)
            page.wait_for_timeout(300)
        else:
            page.wait_for_event("close", timeout=0)
        try:
            b.close()
        except Exception:
            pass
    return rec.events


def to_process(url: str, events: List[Dict[str, Any]]) -> Dict[str, Any]:
    nodes: List[Dict[str, Any]] = [{"id": "start", "type": "core.start", "params": {}},
                                   {"id": "open", "type": "web.open", "label": "Открыть браузер",
                                    "params": {"browser": "chromium", "headless": False, "url": url}}]
    variables = []
    for i, ev in enumerate(events, 1):
        nid = f"r{i}"
        if ev["type"] == "click":
            nodes.append({"id": nid, "type": "web.click", "label": f"Клик: {ev.get('text') or ev['selector']}"[:60],
                          "params": {"selector": ev["selector"]}})
        elif ev["type"] == "fill":
            if ev.get("secret"):
                value = "{{ secret('пароль_портала') }}"
            else:
                var = f"поле_{i}"
                variables.append({"name": var, "default": ev.get("value", "")})
                value = "{{ " + var + " }}"
            nodes.append({"id": nid, "type": "web.fill", "label": f"Ввести: {ev['selector']}"[:60],
                          "params": {"selector": ev["selector"], "value": value}})
        elif ev["type"] == "select":
            nodes.append({"id": nid, "type": "web.select", "params": {"selector": ev["selector"], "value": ev["value"]}})
        elif ev["type"] == "press":
            nodes.append({"id": nid, "type": "web.press", "params": {"selector": ev["selector"], "key": ev["key"]}})
        elif ev["type"] == "goto":
            nodes.append({"id": nid, "type": "web.goto", "params": {"url": ev["url"]}})
        elif ev["type"] == "upload":
            nodes.append({"id": nid, "type": "web.upload", "params": {"selector": ev["selector"], "files": "C:/путь/к/файлу"}})
    nodes += [{"id": "shot", "type": "web.screenshot", "label": "Скриншот результата", "params": {"path": "result.png"}},
              {"id": "close", "type": "web.close", "params": {}}, {"id": "end", "type": "core.end", "params": {}}]
    edges = [{"id": f"e{i}", "source": a["id"], "sourceHandle": "out", "target": b["id"]}
             for i, (a, b) in enumerate(zip(nodes, nodes[1:]))]
    for i, n in enumerate(nodes):
        n["position"] = {"x": 60 + (i % 5) * 330, "y": 60 + (i // 5) * 150}
    return {"variables": variables, "outputs": [], "nodes": nodes, "edges": edges}


def main(args: Any) -> int:
    events = record(args.url, browser=args.browser)
    definition = to_process(args.url, events)
    print(f"Записано действий: {len(events)}")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(definition, f, ensure_ascii=False, indent=2)
        print("Процесс сохранён:", args.out)
    if args.server:
        import httpx

        headers = {"Authorization": f"Bearer {args.token}"} if args.token else {}
        r = httpx.post(f"{args.server.rstrip('/')}/api/processes/import", headers=headers, timeout=30,
                       json={"name": args.name, "description": f"Записано рекордером с {args.url}",
                             "definition": definition})
        r.raise_for_status()
        print(f"Процесс «{r.json()['name']}» создан в оркестраторе: {args.server.rstrip('/')}/#/designer/{r.json()['id']}")
    if not args.out and not args.server:
        print(json.dumps(definition, ensure_ascii=False, indent=2))
    return 0

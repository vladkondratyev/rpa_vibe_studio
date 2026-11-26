"""Блоки автоматизации настольных приложений.

Селектор элемента окна — пары «ключ=значение» через точку с запятой:

    app=Калькулятор; role=button; name=Равно
    window=Платёжное поручение.*; id=txtAmount
    app=Calculator; role=AXButton; name=7; index=0

Ключи: app (имя приложения/процесса), window (заголовок окна, регулярное
выражение), role (тип: button, edit, text, checkbox, menuitem… или
родной AXButton / Button), name (имя, подпись или описание элемента),
id (AutomationId в Windows, AXIdentifier в macOS), value (текущее значение),
index (номер среди найденных, с 0).

macOS: Accessibility API через JXA (osascript). Терминалу или службе робота
нужен доступ в «Настройки → Конфиденциальность → Универсальный доступ».
Windows: UI Automation через pywinauto (pip install pywinauto).
Блок «Инспектор элементов» печатает дерево окна с готовыми селекторами.
"""
from __future__ import annotations

import json
import os
import platform
import re
import shlex
import subprocess
import time
from typing import Any, Dict, List, Optional

from ..context import Context
from ..registry import Output, Param, block

P = Dict[str, Any]
CAT = "Desktop"
SYSTEM = platform.system()

SELECTOR = Param("selector", "Селектор элемента", required=True,
                 help="app=Калькулятор; role=button; name=Равно — см. «Инспектор элементов»")
TIMEOUT = Param("timeout", "Таймаут, сек", "number", default=10)

# Синонимы ролей: короткое имя → роли macOS (AX…) и типы элементов UIA.
ROLES = {
    "button": (["AXButton"], ["Button"]),
    "edit": (["AXTextField", "AXTextArea", "AXComboBox"], ["Edit", "Document"]),
    "text": (["AXStaticText"], ["Text"]),
    "checkbox": (["AXCheckBox"], ["CheckBox"]),
    "radio": (["AXRadioButton"], ["RadioButton"]),
    "combobox": (["AXComboBox", "AXPopUpButton"], ["ComboBox"]),
    "menuitem": (["AXMenuItem", "AXMenuBarItem"], ["MenuItem"]),
    "list": (["AXList", "AXTable", "AXOutline"], ["List", "Table", "DataGrid", "Tree"]),
    "listitem": (["AXRow", "AXCell"], ["ListItem", "DataItem", "TreeItem"]),
    "tab": (["AXRadioButton", "AXTab"], ["TabItem"]),
    "window": (["AXWindow"], ["Window"]),
    "link": (["AXLink"], ["Hyperlink"]),
}


def parse_selector(selector: str) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for part in re.split(r";\s*", selector.strip()):
        if not part.strip():
            continue
        key, sep, value = part.partition("=")
        if not sep:
            raise ValueError(f"Часть селектора без «=»: {part!r}")
        result[key.strip().lower()] = value.strip()
    unknown = set(result) - {"app", "window", "role", "name", "id", "value", "index"}
    if unknown:
        raise ValueError(f"Неизвестные ключи селектора: {', '.join(sorted(unknown))}")
    return result


# ---------------------------------------------------------------- macOS (JXA)
_JXA = r"""
function run(argv) {
  const q = JSON.parse(argv[0]);
  const se = Application('System Events');
  let procs = q.app ? se.processes.whose({_or: [{name: q.app}, {displayedName: q.app}]})() : se.processes.whose({frontmost: true})();
  if (!procs.length) throw new Error('Приложение не запущено: ' + (q.app || 'активное'));
  const proc = procs[0];
  let wins = proc.windows();
  if (q.window) { const re = new RegExp(q.window); wins = wins.filter(w => { try { return re.test(w.name() || ''); } catch (e) { return false; } }); }
  if (!wins.length) throw new Error('Окно не найдено: ' + (q.window || '(любое)'));
  const s = (f) => { try { const v = f(); return v === null || v === undefined ? '' : String(v); } catch (e) { return ''; } };
  const info = (el) => ({role: s(() => el.role()), name: s(() => el.name()), title: s(() => el.title()),
                         description: s(() => el.description()), value: s(() => el.value()),
                         id: s(() => el.attributes.byName('AXIdentifier').value())});
  if (q.action === 'tree') {
    const out = [];
    const walk = (el, depth) => {
      if (out.length >= q.limit) return;
      out.push(Object.assign({depth: depth}, info(el)));
      if (depth >= q.depth) return;
      let kids = []; try { kids = el.uiElements(); } catch (e) {}
      for (const k of kids) walk(k, depth + 1);
    };
    walk(wins[0], 0);
    return JSON.stringify({app: proc.name(), window: s(() => wins[0].name()), items: out});
  }
  const matches = [];
  for (const w of wins) {
    let all = [];
    try { all = w.entireContents(); } catch (e) { all = w.uiElements(); }
    for (const el of all) {
      // атрибуты читаются лениво: каждое чтение — отдельный Apple Event
      if (q.roles.length && q.roles.indexOf(s(() => el.role())) < 0) continue;
      if (q.id !== undefined && s(() => el.attributes.byName('AXIdentifier').value()) !== q.id) continue;
      if (q.name !== undefined) {
        const n = s(() => el.name());
        if (n !== q.name && s(() => el.description()) !== q.name && s(() => el.title()) !== q.name) continue;
      }
      if (q.value !== undefined && s(() => el.value()) !== q.value) continue;
      matches.push([el, null]);
      if (matches.length > q.index && q.action !== 'count') break;
    }
    if (matches.length > q.index) break;
  }
  if (q.action === 'count') return JSON.stringify({count: matches.length});
  if (matches.length <= q.index) return JSON.stringify({found: false});
  const el = matches[q.index][0];
  const i = info(el);
  if (q.action === 'click') {
    try { el.actions.byName('AXPress').perform(); } catch (e) { el.click(); }
  } else if (q.action === 'set') {
    try { el.focused = true; } catch (e) {}
    el.value = q.text;
  } else if (q.action === 'focus') {
    try { el.focused = true; } catch (e) {}
  }
  return JSON.stringify({found: true, element: i});
}
"""


def _mac_roles(role: Optional[str]) -> List[str]:
    if not role:
        return []
    return ROLES.get(role.lower(), ([role], []))[0]


def _jxa(query: Dict[str, Any], timeout: float = 60) -> Dict[str, Any]:
    proc = subprocess.run(["osascript", "-l", "JavaScript", "-e", _JXA, json.dumps(query, ensure_ascii=False)],
                          capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        err = proc.stderr.strip()
        if "-1719" in err or "-25211" in err or "assistive" in err.lower():
            err += " — разрешите доступ в «Универсальный доступ» для терминала или службы робота"
        raise RuntimeError(err.replace("execution error: Error: ", ""))
    return json.loads(proc.stdout.strip() or "{}")


def _mac_query(sel: Dict[str, str], action: str, **extra: Any) -> Dict[str, Any]:
    q: Dict[str, Any] = {"action": action, "roles": _mac_roles(sel.get("role")),
                         "index": int(sel.get("index") or 0)}
    for key in ("app", "window", "name", "id", "value"):
        if key in sel:
            q[key] = sel[key]
    q.update(extra)
    return _jxa(q)


def osascript(script: str, timeout: float = 30) -> str:
    """Выполняет AppleScript и возвращает вывод (macOS)."""
    proc = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip())
    return proc.stdout.strip()


def _as_str(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


# ---------------------------------------------------------------- Windows (pywinauto)
def _uia_find(sel: Dict[str, str]) -> List[Any]:
    try:
        from pywinauto import Desktop
    except ImportError:
        raise RuntimeError("Для Windows-селекторов установите pywinauto: pip install pywinauto") from None
    desktop = Desktop(backend="uia")
    windows = []
    for w in desktop.windows():
        title = w.window_text() or ""
        if sel.get("window") and not re.search(sel["window"], title):
            continue
        if sel.get("app"):
            try:
                import psutil  # pywinauto ставит его как зависимость

                exe = psutil.Process(w.process_id()).name().lower()
            except Exception:
                exe = ""
            app = sel["app"].lower()
            if app not in (exe, exe.removesuffix(".exe")) and app not in title.lower():
                continue
        windows.append(w)
    if not windows:
        raise RuntimeError(f"Окно не найдено: {sel.get('window') or sel.get('app') or '(любое)'}")
    types = ROLES.get((sel.get("role") or "").lower(), ([], [sel["role"]] if sel.get("role") else []))[1]
    found = []
    for w in windows:
        if sel.get("role", "").lower() == "window" and not sel.get("name"):
            found.append(w)
            continue
        for el in w.descendants():
            ei = el.element_info
            if types and ei.control_type not in types:
                continue
            if "name" in sel and (ei.name or "") != sel["name"]:
                continue
            if "id" in sel and (ei.automation_id or "") != sel["id"]:
                continue
            if "value" in sel and _uia_value(el) != sel["value"]:
                continue
            found.append(el)
    return found


def _uia_value(el: Any) -> str:
    for getter in ("get_value", "window_text"):
        try:
            v = getattr(el, getter)()
            if v:
                return str(v)
        except Exception:
            continue
    return ""


def _uia_one(sel: Dict[str, str]) -> Any:
    found = _uia_find(sel)
    idx = int(sel.get("index") or 0)
    return found[idx] if len(found) > idx else None


# ---------------------------------------------------------------- общий слой
def find(sel_text: str, action: str = "find", text: str = "") -> Optional[Dict[str, Any]]:
    sel = parse_selector(sel_text)
    if SYSTEM == "Darwin":
        res = _mac_query(sel, action, text=text)
        return res.get("element") if res.get("found") else None
    if SYSTEM == "Windows":
        el = _uia_one(sel)
        if el is None:
            return None
        if action == "click":
            try:
                el.invoke()
            except Exception:
                el.click_input()
        elif action == "set":
            try:
                el.set_edit_text(text)
            except Exception:
                el.set_focus()
                from pywinauto.keyboard import send_keys
                send_keys("^a{BACKSPACE}")
                el.type_keys(text, with_spaces=True, with_newlines=True)
        elif action == "focus":
            el.set_focus()
        ei = el.element_info
        return {"role": ei.control_type, "name": ei.name, "id": ei.automation_id, "value": _uia_value(el)}
    raise RuntimeError("Селекторы окон поддерживаются в Windows и macOS; в Linux используйте блоки Vision")


def wait_for(ctx: Context, sel_text: str, timeout: float, action: str = "find", text: str = "") -> Dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_error: Optional[Exception] = None
    while True:
        ctx.check_cancel()
        try:
            el = find(sel_text, action, text)
            if el is not None:
                return el
        except RuntimeError as exc:  # окно ещё не открылось
            last_error = exc
        if time.monotonic() >= deadline:
            raise RuntimeError(f"Элемент «{sel_text}» не найден за {timeout:g} с"
                               + (f": {last_error}" if last_error else ""))
        time.sleep(0.5)


def send_keys(text: str = "", hotkey: str = "") -> None:
    """Ввод текста или сочетания клавиш в активное окно."""
    if SYSTEM == "Darwin":
        if text:
            osascript(f'tell application "System Events" to keystroke {_as_str(text)}')
        if hotkey:
            osascript(_mac_hotkey(hotkey))
        return
    if SYSTEM == "Windows":
        try:
            from pywinauto.keyboard import send_keys as sk
        except ImportError:
            sk = None
        if sk:
            if text:
                sk(_escape_pwa(text), with_spaces=True, with_newlines=True, pause=0.01)
            if hotkey:
                sk(_pwa_hotkey(hotkey))
            return
    import pyautogui  # Linux или Windows без pywinauto

    if text:
        pyautogui.write(text, interval=0.01)
    if hotkey:
        pyautogui.hotkey(*_split_hotkey(hotkey))


_MAC_KEYCODES = {"enter": 36, "return": 36, "tab": 48, "space": 49, "backspace": 51, "delete": 117, "esc": 53,
                 "escape": 53, "left": 123, "right": 124, "down": 125, "up": 126, "home": 115, "end": 119,
                 "pageup": 116, "pagedown": 121, "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97,
                 "f7": 98, "f8": 100, "f9": 101, "f10": 109, "f11": 103, "f12": 111}
_MAC_MODS = {"cmd": "command down", "command": "command down", "ctrl": "control down", "control": "control down",
             "alt": "option down", "option": "option down", "shift": "shift down"}


def _split_hotkey(hotkey: str) -> List[str]:
    return [k.strip().lower() for k in hotkey.split("+") if k.strip()]


def _mac_hotkey(hotkey: str) -> str:
    keys = _split_hotkey(hotkey)
    mods = [_MAC_MODS[k] for k in keys[:-1] if k in _MAC_MODS]
    key = keys[-1]
    using = f" using {{{', '.join(mods)}}}" if mods else ""
    if key in _MAC_KEYCODES:
        return f'tell application "System Events" to key code {_MAC_KEYCODES[key]}{using}'
    return f'tell application "System Events" to keystroke {_as_str(key)}{using}'


def _pwa_hotkey(hotkey: str) -> str:
    mods = {"ctrl": "^", "control": "^", "alt": "%", "shift": "+", "win": "{VK_LWIN down}"}
    keys = _split_hotkey(hotkey)
    prefix = "".join(mods.get(k, "") for k in keys[:-1])
    key = keys[-1]
    named = {"enter": "{ENTER}", "tab": "{TAB}", "esc": "{ESC}", "escape": "{ESC}", "backspace": "{BACKSPACE}",
             "delete": "{DELETE}", "space": "{SPACE}", "up": "{UP}", "down": "{DOWN}", "left": "{LEFT}",
             "right": "{RIGHT}", "home": "{HOME}", "end": "{END}", "pageup": "{PGUP}", "pagedown": "{PGDN}"}
    if key in named:
        key = named[key]
    elif re.fullmatch(r"f\d{1,2}", key):
        key = "{" + key.upper() + "}"
    return prefix + key + ("{VK_LWIN up}" if "win" in keys[:-1] else "")


def _escape_pwa(text: str) -> str:
    return re.sub(r"([{}()+^%~\[\]])", r"{\1}", text)


# ---------------------------------------------------------------- блоки
@block(
    "desktop.launch", "Запустить приложение", CAT,
    params=[
        Param("app", "Приложение", required=True,
              help="macOS: имя (Calculator) или путь .app; Windows: путь к .exe или команда (calc)"),
        Param("args", "Аргументы", help="Через пробел, кавычки поддерживаются"),
        Param("wait", "Подождать, сек", "number", default=2),
    ],
)
def launch(ctx: Context, p: P) -> None:
    args = shlex.split(p["args"] or "", posix=SYSTEM != "Windows")
    app = p["app"]
    if SYSTEM == "Darwin" and not os.path.isfile(app):
        cmd = ["open", "-a", app] + (["--args", *args] if args else [])
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        osascript(f"tell application {_as_str(app)} to activate")
    else:
        subprocess.Popen([app, *args], shell=SYSTEM == "Windows" and not os.path.isfile(app))
    ctx.log(f"Запущено: {app}")
    time.sleep(float(p["wait"] or 0))


@block(
    "desktop.activate", "Активировать окно", CAT,
    params=[Param("app", "Приложение"), Param("window", "Заголовок окна (рег. выражение)"), TIMEOUT],
)
def activate(ctx: Context, p: P) -> None:
    if not p["app"] and not p["window"]:
        raise ValueError("Укажите приложение или заголовок окна")
    if SYSTEM == "Darwin":
        if p["app"]:
            osascript(f"tell application {_as_str(p['app'])} to activate")
        if p["window"]:  # поднять нужное окно, дождавшись его появления
            proc = f"process {_as_str(p['app'])}" if p["app"] else "(first process whose frontmost is true)"
            script = (f'tell application "System Events" to tell {proc} to perform action "AXRaise" of '
                      f'(first window whose name contains {_as_str(p["window"])})')
            deadline = time.monotonic() + float(p["timeout"] or 10)
            while True:
                ctx.check_cancel()
                try:
                    osascript(script)
                    break
                except RuntimeError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError(f"Окно «{p['window']}» не найдено") from None
                    time.sleep(0.5)
        return
    sel = "; ".join(f"{k}={v}" for k, v in (("app", p["app"]), ("window", p["window"])) if v) + "; role=window"
    wait_for(ctx, sel, float(p["timeout"] or 10), "focus")


@block(
    "desktop.click", "Нажать элемент", CAT, params=[SELECTOR, TIMEOUT],
    description="Нажимает кнопку, пункт меню, флажок и т.п. по селектору (без координат мыши).",
)
def click(ctx: Context, p: P) -> None:
    el = wait_for(ctx, p["selector"], float(p["timeout"] or 10), "click")
    ctx.log(f"Нажато: {el.get('name') or el.get('description') or el.get('role')}")


@block(
    "desktop.set_text", "Ввести в поле", CAT,
    params=[SELECTOR, Param("text", "Текст", required=True), TIMEOUT],
)
def set_text(ctx: Context, p: P) -> None:
    wait_for(ctx, p["selector"], float(p["timeout"] or 10), "set", str(p["text"]))


@block(
    "desktop.get_text", "Прочитать элемент", CAT,
    params=[SELECTOR, Param("result", "Записать в", "variable", required=True, default="text"), TIMEOUT],
    description="Значение поля или подпись элемента.",
)
def get_text(ctx: Context, p: P) -> None:
    el = wait_for(ctx, p["selector"], float(p["timeout"] or 10))
    ctx.vars[p["result"]] = el.get("value") or el.get("name") or el.get("title") or el.get("description") or ""


@block(
    "desktop.wait", "Ждать элемент", CAT, params=[SELECTOR, TIMEOUT],
)
def wait(ctx: Context, p: P) -> None:
    wait_for(ctx, p["selector"], float(p["timeout"] or 10))


@block(
    "desktop.exists", "Элемент есть?", CAT,
    params=[SELECTOR, Param("timeout", "Ждать, сек", "number", default=0)],
    outputs=[Output("true", "Да"), Output("false", "Нет")],
)
def exists(ctx: Context, p: P) -> str:
    try:
        wait_for(ctx, p["selector"], float(p["timeout"] or 0))
        return "true"
    except RuntimeError:
        return "false"


@block(
    "desktop.type", "Ввести с клавиатуры", CAT,
    params=[Param("text", "Текст"), Param("hotkey", "Сочетание клавиш", help="ctrl+s, cmd+v, enter, alt+f4")],
    description="Печатает текст и/или нажимает сочетание клавиш в активном окне.",
)
def type_(ctx: Context, p: P) -> None:
    if not p["text"] and not p["hotkey"]:
        raise ValueError("Укажите текст или сочетание клавиш")
    send_keys(str(p["text"] or ""), p["hotkey"] or "")


@block(
    "desktop.close", "Закрыть приложение", CAT,
    params=[Param("app", "Приложение", required=True),
            Param("force", "Принудительно", "boolean", default=False)],
)
def close(ctx: Context, p: P) -> None:
    app = p["app"]
    if SYSTEM == "Darwin":
        if p["force"]:
            subprocess.run(["pkill", "-x", app], capture_output=True)
        else:
            osascript(f"tell application {_as_str(app)} to quit")
    elif SYSTEM == "Windows":
        exe = app if app.lower().endswith(".exe") else app + ".exe"
        subprocess.run(["taskkill", "/IM", os.path.basename(exe)] + (["/F"] if p["force"] else []),
                       capture_output=True)
    else:
        subprocess.run(["pkill", "-9" if p["force"] else "-15", "-f", app], capture_output=True)


@block(
    "desktop.inspect", "Инспектор элементов", CAT,
    params=[Param("app", "Приложение"), Param("window", "Заголовок окна (рег. выражение)"),
            Param("depth", "Глубина", "number", default=6),
            Param("result", "Записать в", "variable", default="elements")],
    description="Пишет в журнал дерево элементов окна с готовыми селекторами — для настройки процесса.",
)
def inspect(ctx: Context, p: P) -> None:
    items = inspect_tree(p["app"] or "", p["window"] or "", int(p["depth"] or 6))
    lines = [("  " * it["depth"]) + it["selector"] for it in items]
    ctx.log("Элементы окна:\n" + "\n".join(lines[:400]))
    if p["result"]:
        ctx.vars[p["result"]] = items


def inspect_tree(app: str, window: str, depth: int = 6, limit: int = 1500) -> List[Dict[str, Any]]:
    head = "; ".join(f"{k}={v}" for k, v in (("app", app), ("window", window)) if v)
    out: List[Dict[str, Any]] = []
    if SYSTEM == "Darwin":
        q: Dict[str, Any] = {"action": "tree", "roles": [], "index": 0, "depth": depth, "limit": limit}
        if app:
            q["app"] = app
        if window:
            q["window"] = window
        res = _jxa(q, timeout=120)
        head = head or f"app={res.get('app')}"
        for it in res.get("items", []):
            name = it.get("name") or it.get("title") or it.get("description")
            parts = [head, f"role={_short_role(it['role'], 0)}"]
            if it.get("id"):
                parts.append(f"id={it['id']}")
            elif name:
                parts.append(f"name={name}")
            it["selector"] = "; ".join(parts) + (f"   [значение: {it['value'][:40]}]" if it.get("value") else "")
            out.append(it)
        return out
    if SYSTEM == "Windows":
        sel = parse_selector(head + "; role=window") if head else {"role": "window"}
        for w in _uia_find(sel)[:1]:
            def walk(el: Any, d: int) -> None:
                if len(out) >= limit:
                    return
                ei = el.element_info
                parts = [head or f"window={re.escape(w.window_text())}", f"role={_short_role(ei.control_type, 1)}"]
                if ei.automation_id:
                    parts.append(f"id={ei.automation_id}")
                elif ei.name:
                    parts.append(f"name={ei.name}")
                out.append({"depth": d, "role": ei.control_type, "name": ei.name, "id": ei.automation_id,
                            "value": _uia_value(el), "selector": "; ".join(parts)})
                if d < depth:
                    for child in el.children():
                        walk(child, d + 1)
            walk(w, 0)
        return out
    raise RuntimeError("Инспектор поддерживается в Windows и macOS")


def _short_role(native: str, platform_index: int) -> str:
    for short, roles in ROLES.items():
        if native in roles[platform_index]:
            return short
    return native

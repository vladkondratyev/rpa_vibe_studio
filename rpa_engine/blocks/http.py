"""Интеграции по сети: REST/HTTP, SOAP, 1С (OData), разбор XML и JSON."""
from __future__ import annotations

import json
import os
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from ..context import Context
from ..registry import Output, Param, block

P = Dict[str, Any]
CAT = "Интеграции"


def _as_dict(value: Any, what: str) -> Dict[str, Any]:
    if value in (None, ""):
        return {}
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        raise ValueError(f"{what}: ожидается JSON-объект") from None
    if not isinstance(parsed, dict):
        raise ValueError(f"{what}: ожидается JSON-объект")
    return parsed


def _client(p: P) -> Any:
    import httpx

    auth = (p["username"], p.get("password") or "") if p.get("username") else None
    return httpx.Client(timeout=float(p.get("timeout") or 60), verify=bool(p.get("verify", True)),
                        auth=auth, follow_redirects=True)


AUTH = [Param("username", "Логин (Basic)"), Param("password", "Пароль (секрет)", "secret",
                                                    help="Имя секрета из хранилища")]


@block(
    "http.request", "HTTP-запрос", CAT,
    params=[
        Param("method", "Метод", "select", default="GET", options=["GET", "POST", "PUT", "PATCH", "DELETE"]),
        Param("url", "Адрес", required=True),
        Param("headers", "Заголовки (JSON)", "text", help='{"Authorization": "Bearer {{ secret(\'api\') }}"}'),
        Param("query", "Параметры строки (JSON)", "text"),
        Param("body_type", "Тело", "select", default="json", options=["json", "form", "text", "file"]),
        Param("body", "Содержимое тела", "text", help="JSON, текст или путь к файлу"),
        *AUTH,
        Param("timeout", "Таймаут, сек", "number", default=60),
        Param("verify", "Проверять сертификат", "boolean", default=True),
        Param("fail_on_error", "Ошибка при коде ≥ 400", "boolean", default=True),
        Param("save_to", "Сохранить ответ в файл"),
        Param("result", "Ответ в переменную", "variable", default="response"),
    ],
    description="Результат: {status, headers, json, text}. Ответ JSON разбирается автоматически.",
)
def request(ctx: Context, p: P) -> None:
    kwargs: Dict[str, Any] = {"headers": _as_dict(p["headers"], "Заголовки"),
                              "params": _as_dict(p["query"], "Параметры")}
    body = p["body"]
    if body not in (None, ""):
        kind = p["body_type"] or "json"
        if kind == "json":
            kwargs["json"] = body if isinstance(body, (dict, list)) else json.loads(body)
        elif kind == "form":
            kwargs["data"] = _as_dict(body, "Тело формы")
        elif kind == "file":
            kwargs["content"] = open(os.path.expanduser(str(body)), "rb").read()
        else:
            kwargs["content"] = str(body).encode()
    with _client(p) as c:
        r = c.request(p["method"] or "GET", p["url"], **kwargs)
    if p["save_to"]:
        with open(os.path.expanduser(p["save_to"]), "wb") as f:
            f.write(r.content)
    out: Dict[str, Any] = {"status": r.status_code, "headers": dict(r.headers), "text": None, "json": None}
    ctype = r.headers.get("content-type", "")
    if "json" in ctype:
        try:
            out["json"] = r.json()
        except ValueError:
            pass
    if not p["save_to"] and ("text" in ctype or "json" in ctype or "xml" in ctype or not ctype):
        out["text"] = r.text
    if p["result"]:
        ctx.vars[p["result"]] = out
    ctx.log(f"{p['method'] or 'GET'} {p['url']} → {r.status_code}")
    if p["fail_on_error"] and r.status_code >= 400:
        raise RuntimeError(f"Сервер ответил {r.status_code}: {r.text[:500]}")


@block(
    "http.soap", "SOAP-вызов", CAT,
    params=[
        Param("url", "Адрес сервиса", required=True),
        Param("action", "SOAPAction"),
        Param("envelope", "Конверт (XML)", "text", required=True,
              default='<?xml version="1.0" encoding="utf-8"?>\n'
                      '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">\n'
                      '  <soap:Body>\n  </soap:Body>\n</soap:Envelope>'),
        Param("version", "Версия SOAP", "select", default="1.1", options=["1.1", "1.2"]),
        *AUTH,
        Param("timeout", "Таймаут, сек", "number", default=60),
        Param("result", "Ответ (словарь) в", "variable", default="soap_result"),
    ],
    description="Отправляет конверт и разбирает ответ в словарь (без пространств имён).",
)
def soap(ctx: Context, p: P) -> None:
    if p["version"] == "1.2":
        headers = {"Content-Type": f'application/soap+xml; charset=utf-8; action="{p["action"] or ""}"'}
    else:
        headers = {"Content-Type": "text/xml; charset=utf-8", "SOAPAction": f'"{p["action"] or ""}"'}
    with _client(p) as c:
        r = c.post(p["url"], content=str(p["envelope"]).encode("utf-8"), headers=headers)
    if r.status_code >= 400:
        fault = _fault(r.text)
        raise RuntimeError(f"SOAP {r.status_code}: {fault or r.text[:500]}")
    root = ET.fromstring(r.content)
    body = next((el for el in root.iter() if _local(el.tag) == "Body"), root)
    ctx.vars[p["result"]] = xml_to_dict(body[0]) if len(body) else {}


def _fault(text: str) -> Optional[str]:
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None
    for el in root.iter():
        if _local(el.tag) in ("faultstring", "Text"):
            return el.text
    return None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def xml_to_dict(el: ET.Element) -> Any:
    """XML → dict: атрибуты с «@», повторяющиеся теги — списком, текст — «#text»."""
    children = list(el)
    node: Dict[str, Any] = {f"@{_local(k)}": v for k, v in el.attrib.items()}
    if not children:
        text = (el.text or "").strip()
        if not node:
            return text
        if text:
            node["#text"] = text
        return node
    for child in children:
        key, val = _local(child.tag), xml_to_dict(child)
        if key in node:
            if not isinstance(node[key], list):
                node[key] = [node[key]]
            node[key].append(val)
        else:
            node[key] = val
    return node


@block(
    "data.parse_xml", "Разобрать XML", CAT,
    params=[Param("source", "XML или путь к файлу", "string", required=True),
            Param("xpath", "Путь (ElementTree XPath)", help="Например .//Valute — список совпадений"),
            Param("encoding", "Кодировка файла", default="auto"),
            Param("result", "Записать в", "variable", required=True, default="data")],
)
def parse_xml(ctx: Context, p: P) -> None:
    src = p["source"]
    if isinstance(src, str) and not src.lstrip().startswith("<") and os.path.exists(os.path.expanduser(src)):
        raw = open(os.path.expanduser(src), "rb").read()
    else:
        raw = src.encode("utf-8") if isinstance(src, str) else src
        if isinstance(src, str):  # уже декодированная строка: убираем объявление кодировки
            import re

            raw = re.sub(rb"^\s*<\?xml[^>]*\?>", b"", raw)
    root = ET.fromstring(raw)
    if p["xpath"]:
        ctx.vars[p["result"]] = [xml_to_dict(e) for e in _strip_ns(root).findall(p["xpath"])]
    else:
        ctx.vars[p["result"]] = {_local(root.tag): xml_to_dict(root)}


def _strip_ns(root: ET.Element) -> ET.Element:
    for el in root.iter():
        el.tag = _local(el.tag)
    return root


@block(
    "data.parse_json", "Разобрать JSON", CAT,
    params=[Param("source", "JSON или путь к файлу", "string", required=True),
            Param("result", "Записать в", "variable", required=True, default="data")],
)
def parse_json(ctx: Context, p: P) -> None:
    src = p["source"]
    if isinstance(src, (dict, list)):
        ctx.vars[p["result"]] = src
        return
    if not src.lstrip().startswith(("{", "[")) and os.path.exists(os.path.expanduser(src)):
        with open(os.path.expanduser(src), encoding="utf-8-sig") as f:
            ctx.vars[p["result"]] = json.load(f)
    else:
        ctx.vars[p["result"]] = json.loads(src)


# ---------------------------------------------------------------- 1С
ONEC = [Param("base_url", "Адрес OData базы", required=True,
              help="http://srv/base/odata/standard.odata — публикация базы на веб-сервере"),
        Param("username", "Пользователь 1С"), Param("password", "Пароль (секрет)", "secret"),
        Param("timeout", "Таймаут, сек", "number", default=60), Param("verify", "Проверять сертификат", "boolean",
                                                                       default=True)]


@block(
    "onec.query", "1С: получить данные", CAT,
    params=[*ONEC,
            Param("entity", "Объект", required=True, help="Catalog_Контрагенты, Document_СчетНаОплатуПокупателю"),
            Param("filter", "Отбор ($filter)", help="ИНН eq '7707083893' and DeletionMark eq false"),
            Param("select", "Поля ($select)", help="Ref_Key,Description,ИНН"),
            Param("top", "Не больше", "number", default=1000),
            Param("result", "Записать в", "variable", required=True, default="rows")],
    description="Чтение справочников, документов и регистров через стандартный OData-интерфейс 1С.",
)
def onec_query(ctx: Context, p: P) -> None:
    url = f"{p['base_url'].rstrip('/')}/{quote(p['entity'])}"
    params = {"$format": "json", "$top": str(int(p["top"] or 1000))}
    if p["filter"]:
        params["$filter"] = p["filter"]
    if p["select"]:
        params["$select"] = p["select"]
    with _client(p) as c:
        r = c.get(url, params=params)
    if r.status_code >= 400:
        raise RuntimeError(f"1С ответила {r.status_code}: {r.text[:500]}")
    rows = r.json().get("value", [])
    ctx.vars[p["result"]] = rows
    ctx.log(f"1С {p['entity']}: получено записей {len(rows)}")


@block(
    "onec.create", "1С: создать / изменить", CAT,
    params=[*ONEC,
            Param("entity", "Объект", required=True),
            Param("key", "Ref_Key для изменения", help="Пусто — создать новый объект"),
            Param("data", "Поля (JSON)", "text", required=True),
            Param("post", "Провести документ", "boolean", default=False),
            Param("result", "Ответ в", "variable", default="onec_object")],
)
def onec_create(ctx: Context, p: P) -> None:
    base = p["base_url"].rstrip("/")
    data = p["data"] if isinstance(p["data"], dict) else json.loads(p["data"])
    with _client(p) as c:
        if p["key"]:
            url = f"{base}/{quote(p['entity'])}(guid'{p['key']}')"
            r = c.patch(url, params={"$format": "json"}, json=data)
        else:
            r = c.post(f"{base}/{quote(p['entity'])}", params={"$format": "json"}, json=data)
        if r.status_code >= 400:
            raise RuntimeError(f"1С ответила {r.status_code}: {r.text[:500]}")
        obj = r.json()
        if p["post"]:
            ref = obj.get("Ref_Key") or p["key"]
            rp = c.post(f"{base}/{quote(p['entity'])}(guid'{ref}')/Post", params={"$format": "json"})
            if rp.status_code >= 400:
                raise RuntimeError(f"Документ не проведён: {rp.text[:500]}")
    if p["result"]:
        ctx.vars[p["result"]] = obj

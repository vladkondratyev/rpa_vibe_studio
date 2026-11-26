"""Экосистема Яндекса: Диск, Vision OCR, YandexGPT, SpeechKit, Переводчик,
Трекер, Мессенджер. Object Storage — в модуле storage (S3)."""
from __future__ import annotations

import base64
import os
from typing import Any, Dict, Optional

from .. import llm, ocr
from ..context import Context
from ..registry import Output, Param, block

P = Dict[str, Any]
CAT = "Яндекс"
DISK = "https://cloud-api.yandex.net/v1/disk"

CLOUD = [Param("api_key", "API-ключ Yandex Cloud (секрет)", "secret", required=True, default="yandex_api_key",
               help="Ключ сервисного аккаунта или IAM-токен (t1.…)"),
         Param("folder_id", "Каталог (folder_id)", required=True)]
OAUTH = Param("token", "OAuth-токен (секрет)", "secret", required=True, default="yandex_oauth")


def _cloud(p: P) -> Dict[str, Any]:
    return {"api_key": p["api_key"], "folder_id": p["folder_id"]}


def _http(token: str, method: str, url: str, **kw: Any) -> Any:
    import httpx

    r = httpx.request(method, url, headers={"Authorization": f"OAuth {token}"}, timeout=120, **kw)
    if r.status_code >= 400:
        try:
            msg = r.json().get("message") or r.json().get("description") or r.text
        except ValueError:
            msg = r.text
        raise RuntimeError(f"Яндекс {r.status_code}: {str(msg)[:300]}")
    return r


# ---------------------------------------------------------------- Яндекс Диск
@block("yadisk.upload", "Яндекс Диск: загрузить", CAT,
       params=[OAUTH, Param("file", "Локальный файл", required=True),
               Param("path", "Путь на Диске", required=True, help="disk:/Отчёты/выписка.xlsx или /Отчёты/"),
               Param("overwrite", "Перезаписать", "boolean", default=True)])
def disk_upload(ctx: Context, p: P) -> None:
    import httpx

    local = os.path.expanduser(p["file"])
    target = p["path"]
    if target.endswith("/"):
        target += os.path.basename(local)
    href = _http(p["token"], "GET", f"{DISK}/resources/upload",
                 params={"path": target, "overwrite": str(bool(p["overwrite"])).lower()}).json()["href"]
    with open(local, "rb") as f:
        r = httpx.put(href, content=f.read(), timeout=600)
    if r.status_code >= 400:
        raise RuntimeError(f"Яндекс Диск: загрузка не удалась ({r.status_code})")
    ctx.log(f"Загружено на Диск: {target}")


@block("yadisk.download", "Яндекс Диск: скачать", CAT,
       params=[OAUTH, Param("path", "Путь на Диске", required=True), Param("file", "Сохранить как", required=True)])
def disk_download(ctx: Context, p: P) -> None:
    import httpx

    href = _http(p["token"], "GET", f"{DISK}/resources/download", params={"path": p["path"]}).json()["href"]
    r = httpx.get(href, timeout=600, follow_redirects=True)
    r.raise_for_status()
    with open(os.path.expanduser(p["file"]), "wb") as f:
        f.write(r.content)


@block("yadisk.list", "Яндекс Диск: список файлов", CAT,
       params=[OAUTH, Param("path", "Папка", required=True, default="disk:/"),
               Param("limit", "Не больше", "number", default=200),
               Param("result", "Файлы в", "variable", required=True, default="disk_files")])
def disk_list(ctx: Context, p: P) -> None:
    data = _http(p["token"], "GET", f"{DISK}/resources",
                 params={"path": p["path"], "limit": int(p["limit"] or 200)}).json()
    ctx.vars[p["result"]] = [{"name": i["name"], "path": i["path"], "type": i["type"], "size": i.get("size"),
                              "modified": i.get("modified")} for i in data.get("_embedded", {}).get("items", [])]


@block("yadisk.mkdir", "Яндекс Диск: создать папку", CAT,
       params=[OAUTH, Param("path", "Папка", required=True)])
def disk_mkdir(ctx: Context, p: P) -> None:
    import httpx

    r = httpx.put(f"{DISK}/resources", params={"path": p["path"]},
                  headers={"Authorization": f"OAuth {p['token']}"}, timeout=60)
    if r.status_code not in (201, 409):  # 409 — уже существует
        raise RuntimeError(f"Яндекс Диск: {r.text[:300]}")


@block("yadisk.publish", "Яндекс Диск: публичная ссылка", CAT,
       params=[OAUTH, Param("path", "Путь на Диске", required=True),
               Param("result", "Ссылка в", "variable", required=True, default="public_url")])
def disk_publish(ctx: Context, p: P) -> None:
    _http(p["token"], "PUT", f"{DISK}/resources/publish", params={"path": p["path"]})
    meta = _http(p["token"], "GET", f"{DISK}/resources", params={"path": p["path"], "fields": "public_url"}).json()
    ctx.vars[p["result"]] = meta.get("public_url")


# ---------------------------------------------------------------- Yandex Cloud AI
@block("yandex.vision_ocr", "Yandex Vision: распознать", CAT,
       params=[*CLOUD, Param("file", "Изображение или PDF", required=True),
               Param("model", "Модель", "select", default="page",
                     options=["page", "page-column-sort", "handwritten", "table", "passport", "driver-license-front",
                              "driver-license-back", "vehicle-registration-front", "license-plates"],
                     help="passport и driver-license-* сразу возвращают поля документа"),
               Param("lang", "Языки", default="rus+eng"),
               Param("result", "Результат в", "variable", required=True, default="vision")],
       description="Результат: {text, entities} — для моделей документов entities содержит поля (фамилия, номер…).")
def vision_ocr(ctx: Context, p: P) -> None:
    with open(os.path.expanduser(p["file"]), "rb") as f:
        res = ocr.yandex_vision(f.read(), _cloud(p), p["model"] or "page", p["lang"] or "rus+eng")
    ctx.vars[p["result"]] = {"text": res["text"], "entities": res["entities"]}


@block("yandex.gpt", "YandexGPT", CAT,
       params=[*CLOUD, Param("model", "Модель", default="yandexgpt/latest",
                             help="yandexgpt/latest, yandexgpt-lite/latest, yandexgpt/rc или gpt://…"),
               Param("system", "Инструкция", "text"), Param("prompt", "Запрос", "text", required=True),
               Param("temperature", "Температура", "number", default=0.2),
               Param("result", "Ответ в", "variable", required=True, default="answer")])
def gpt(ctx: Context, p: P) -> None:
    conn = dict(_cloud(p), provider="yandexgpt", model=p["model"] or "yandexgpt/latest")
    ctx.vars[p["result"]] = llm.chat(conn, str(p["prompt"]), p["system"] or "",
                                     temperature=float(p["temperature"] if p["temperature"] is not None else 0.2)).strip()


@block("yandex.translate", "Яндекс Переводчик", CAT,
       params=[*CLOUD, Param("text", "Текст", required=True), Param("target", "На язык", default="ru"),
               Param("source", "С языка", help="Пусто — определить автоматически"),
               Param("result", "Перевод в", "variable", required=True, default="translation")])
def translate(ctx: Context, p: P) -> None:
    import httpx

    body: Dict[str, Any] = {"targetLanguageCode": p["target"] or "ru", "texts": [str(p["text"])],
                            "folderId": p["folder_id"]}
    if p["source"]:
        body["sourceLanguageCode"] = p["source"]
    r = httpx.post("https://translate.api.cloud.yandex.net/translate/v2/translate", json=body,
                   headers=llm.yandex_headers(_cloud(p)), timeout=60)
    if r.status_code >= 400:
        raise RuntimeError(f"Переводчик {r.status_code}: {r.text[:300]}")
    ctx.vars[p["result"]] = r.json()["translations"][0]["text"]


@block("yandex.speech_to_text", "SpeechKit: распознать речь", CAT,
       params=[*CLOUD, Param("file", "Аудио (OGG/Opus, WAV LPCM)", required=True, help="До 30 с и 1 МБ"),
               Param("lang", "Язык", default="ru-RU"),
               Param("format", "Формат", "select", default="oggopus", options=["oggopus", "lpcm"]),
               Param("result", "Текст в", "variable", required=True, default="speech_text")],
       description="Короткие записи: голосовые сообщения, фрагменты звонков. Для длинных — асинхронный API.")
def speech_to_text(ctx: Context, p: P) -> None:
    import httpx

    with open(os.path.expanduser(p["file"]), "rb") as f:
        audio = f.read()
    params = {"lang": p["lang"] or "ru-RU", "folderId": p["folder_id"], "format": p["format"] or "oggopus"}
    if params["format"] == "lpcm":
        params["sampleRateHertz"] = "48000"
    r = httpx.post("https://stt.api.cloud.yandex.net/speech/v1/stt:recognize", params=params, content=audio,
                   headers=llm.yandex_headers(_cloud(p)), timeout=120)
    if r.status_code >= 400:
        raise RuntimeError(f"SpeechKit {r.status_code}: {r.text[:300]}")
    ctx.vars[p["result"]] = r.json().get("result", "")


# ---------------------------------------------------------------- Трекер и Мессенджер
@block("yandex.tracker_issue", "Яндекс Трекер: создать задачу", CAT,
       params=[OAUTH, Param("org_id", "ID организации", required=True),
               Param("org_type", "Тип организации", "select", default="cloud", options=["cloud", "360"]),
               Param("queue", "Очередь", required=True, help="Ключ очереди, например BANKOPS"),
               Param("summary", "Заголовок", required=True), Param("description", "Описание", "text"),
               Param("assignee", "Исполнитель (логин)"),
               Param("result", "Ключ задачи в", "variable", default="issue_key")])
def tracker_issue(ctx: Context, p: P) -> None:
    import httpx

    header = "X-Cloud-Org-ID" if (p["org_type"] or "cloud") == "cloud" else "X-Org-ID"
    body = {"queue": p["queue"], "summary": p["summary"], "description": p["description"] or ""}
    if p["assignee"]:
        body["assignee"] = p["assignee"]
    r = httpx.post("https://api.tracker.yandex.net/v2/issues/", json=body, timeout=60,
                   headers={"Authorization": f"OAuth {p['token']}", header: str(p["org_id"])})
    if r.status_code >= 400:
        raise RuntimeError(f"Трекер {r.status_code}: {r.text[:300]}")
    key = r.json().get("key")
    if p["result"]:
        ctx.vars[p["result"]] = key
    ctx.log(f"Создана задача {key}")


@block("yandex.messenger", "Яндекс Мессенджер: сообщение", CAT,
       params=[Param("token", "OAuth-токен бота (секрет)", "secret", required=True, default="yandex_bot"),
               Param("chat_id", "ID чата"), Param("login", "Или логин сотрудника"),
               Param("text", "Текст", "text", required=True)])
def messenger(ctx: Context, p: P) -> None:
    import httpx

    if not p["chat_id"] and not p["login"]:
        raise ValueError("Укажите ID чата или логин")
    body: Dict[str, Any] = {"text": str(p["text"])}
    body["chat_id" if p["chat_id"] else "login"] = p["chat_id"] or p["login"]
    r = httpx.post("https://botapi.messenger.yandex.net/bot/v1/messages/sendText/", json=body, timeout=60,
                   headers={"Authorization": f"OAuth {p['token']}"})
    if r.status_code >= 400:
        raise RuntimeError(f"Мессенджер {r.status_code}: {r.text[:300]}")

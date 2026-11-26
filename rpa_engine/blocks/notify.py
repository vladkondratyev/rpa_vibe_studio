"""Уведомления: Telegram, произвольный вебхук (Slack, Mattermost, «Пачка», Teams)."""
from __future__ import annotations

import json
import os
from typing import Any, Dict

from ..context import Context
from ..registry import Param, block

P = Dict[str, Any]
CAT = "Уведомления"


def telegram_send(token: str, chat_id: str, text: str, file: str = "", api: str = "") -> None:
    import httpx

    base = (api or os.environ.get("RPA_TELEGRAM_API") or "https://api.telegram.org").rstrip("/")
    url = f"{base}/bot{token}"
    with httpx.Client(timeout=60) as c:
        if file:
            with open(os.path.expanduser(file), "rb") as f:
                r = c.post(f"{url}/sendDocument", data={"chat_id": chat_id, "caption": text[:1024]},
                           files={"document": (os.path.basename(file), f)})
        else:
            r = c.post(f"{url}/sendMessage", json={"chat_id": chat_id, "text": text[:4096],
                                                   "disable_web_page_preview": True})
    if r.status_code >= 400:
        raise RuntimeError(f"Telegram: {r.text[:300]}")


@block(
    "notify.telegram", "Сообщение в Telegram", CAT,
    params=[Param("token", "Токен бота (секрет)", "secret", required=True, default="telegram_bot"),
            Param("chat_id", "ID чата", required=True, help="Узнать: написать боту и открыть getUpdates"),
            Param("text", "Текст", "text", required=True),
            Param("file", "Файл", help="Путь к файлу — отправится документом с подписью"),
            Param("api", "Адрес Bot API", help="Свой сервер Bot API или прокси; пусто — api.telegram.org")],
)
def telegram(ctx: Context, p: P) -> None:
    telegram_send(p["token"], str(p["chat_id"]), str(p["text"]), p["file"] or "", p["api"] or "")


@block(
    "notify.webhook", "Сообщение в вебхук", CAT,
    params=[Param("url", "Адрес вебхука", required=True),
            Param("payload", "Тело (JSON)", "text", required=True, default='{"text": "Робот: готово"}')],
    description="Входящие вебхуки Slack, Mattermost, «Пачки», Teams, Bitrix24 и любых систем.",
)
def webhook(ctx: Context, p: P) -> None:
    import httpx

    payload = p["payload"] if isinstance(p["payload"], (dict, list)) else json.loads(p["payload"])
    r = httpx.post(p["url"], json=payload, timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(f"Вебхук ответил {r.status_code}: {r.text[:300]}")

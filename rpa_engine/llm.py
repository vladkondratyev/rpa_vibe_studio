"""Подключения к языковым моделям: Claude (Anthropic), YandexGPT, GigaChat
и любые OpenAI-совместимые серверы (Ollama, vLLM, LM Studio, локальные модели в контуре банка).

Подключение — словарь:
    {"name": "yandex", "provider": "yandexgpt", "model": "yandexgpt/latest",
     "api_key": "...", "folder_id": "b1g...", "base_url": "", "verify": true}

Подключения хранятся в оркестраторе (раздел «Модели ИИ»), ключи — в хранилище
секретов; робот получает их вместе с задачей. При локальном запуске подключение
по умолчанию собирается из переменных окружения RPA_LLM_*.
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
import uuid
from typing import Any, Dict, List, Optional

PROVIDERS = {
    "anthropic": "Claude (Anthropic)",
    "yandexgpt": "YandexGPT (Yandex Cloud)",
    "gigachat": "GigaChat (Сбер)",
    "openai": "OpenAI-совместимый (Ollama, vLLM, LM Studio…)",
}
DEFAULT_MODELS = {
    "anthropic": "claude-opus-5-5",
    "yandexgpt": "yandexgpt/latest",
    "gigachat": "GigaChat-2-Max",
    "openai": "qwen2.5:14b",
}


class LLMError(RuntimeError):
    pass


def env_connection() -> Optional[Dict[str, Any]]:
    provider = os.environ.get("RPA_LLM_PROVIDER")
    if not provider:
        if os.environ.get("ANTHROPIC_API_KEY"):
            provider = "anthropic"
        else:
            return None
    return {"name": "default", "provider": provider, "model": os.environ.get("RPA_LLM_MODEL", ""),
            "api_key": os.environ.get("RPA_LLM_API_KEY", ""), "base_url": os.environ.get("RPA_LLM_BASE_URL", ""),
            "folder_id": os.environ.get("RPA_LLM_FOLDER_ID", ""),
            "verify": os.environ.get("RPA_LLM_VERIFY", "1") not in ("0", "false")}


def chat(conn: Dict[str, Any], prompt: str, system: str = "", images: Optional[List[bytes]] = None,
         max_tokens: int = 8000, temperature: Optional[float] = None) -> str:
    """Один запрос к модели, возвращает текст ответа."""
    provider = conn.get("provider") or "anthropic"
    fn = {"anthropic": _anthropic, "yandexgpt": _yandex, "gigachat": _gigachat, "openai": _openai}.get(provider)
    if fn is None:
        raise LLMError(f"Неизвестный провайдер ИИ: {provider}")
    model = conn.get("model") or DEFAULT_MODELS[provider]
    return fn(conn, model, prompt, system, images or [], max_tokens, temperature)


# ---------------------------------------------------------------- Claude
def _anthropic(conn, model, prompt, system, images, max_tokens, temperature) -> str:
    try:
        import anthropic
    except ImportError:
        raise LLMError("Нужен пакет anthropic: pip install anthropic") from None
    kwargs: Dict[str, Any] = {}
    if conn.get("api_key"):
        kwargs["api_key"] = conn["api_key"]
    if conn.get("base_url"):
        kwargs["base_url"] = conn["base_url"]
    client = anthropic.Anthropic(**kwargs)
    content: List[Dict[str, Any]] = [
        {"type": "image", "source": {"type": "base64", "media_type": _mime(img),
                                     "data": base64.standard_b64encode(img).decode()}} for img in images]
    content.append({"type": "text", "text": prompt})
    request: Dict[str, Any] = {"model": model, "max_tokens": max_tokens,
                               "messages": [{"role": "user", "content": content}]}
    if system:
        request["system"] = system
    try:
        if model.startswith(("claude-opus-5", "claude-fable-5", "claude-sonnet-5-5")):
            # серверный запасной вариант на случай отказа модели
            response = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"],
                                                   extra_body={"fallbacks": "default"}, **request)
        else:
            response = client.messages.create(**request)
    except anthropic.AuthenticationError:
        raise LLMError("Claude: неверный API-ключ") from None
    except anthropic.RateLimitError:
        raise LLMError("Claude: превышен лимит запросов, повторите позже") from None
    except anthropic.APIStatusError as exc:
        raise LLMError(f"Claude: ошибка {exc.status_code}: {exc.message}") from None
    except anthropic.APIConnectionError:
        raise LLMError("Claude: нет связи с API") from None
    if response.stop_reason == "refusal":
        raise LLMError("Claude отказался выполнять запрос")
    return "".join(b.text for b in response.content if b.type == "text")


# ---------------------------------------------------------------- YandexGPT
def yandex_headers(conn: Dict[str, Any]) -> Dict[str, str]:
    key = conn.get("api_key") or ""
    if not key:
        raise LLMError("Yandex Cloud: не указан API-ключ или IAM-токен")
    auth = f"Bearer {key}" if key.startswith("t1.") else f"Api-Key {key}"
    headers = {"Authorization": auth}
    if conn.get("folder_id"):
        headers["x-folder-id"] = conn["folder_id"]
    return headers


def _yandex(conn, model, prompt, system, images, max_tokens, temperature) -> str:
    import httpx

    if images:
        raise LLMError("YandexGPT не принимает изображения: для документов используйте Yandex Vision OCR")
    folder = conn.get("folder_id")
    if not folder:
        raise LLMError("YandexGPT: укажите идентификатор каталога (folder_id)")
    uri = model if model.startswith(("gpt://", "ds://")) else f"gpt://{folder}/{model}"
    messages = ([{"role": "system", "text": system}] if system else []) + [{"role": "user", "text": prompt}]
    body = {"modelUri": uri, "completionOptions": {"stream": False, "maxTokens": str(min(max_tokens, 8000)),
                                                   "temperature": 0.1 if temperature is None else temperature},
            "messages": messages}
    base = (conn.get("base_url") or "https://llm.api.cloud.yandex.net").rstrip("/")
    r = httpx.post(f"{base}/foundationModels/v1/completion", json=body, headers=yandex_headers(conn),
                   timeout=180, verify=conn.get("verify", True))
    if r.status_code >= 400:
        raise LLMError(f"YandexGPT {r.status_code}: {r.text[:400]}")
    alts = r.json().get("result", {}).get("alternatives", [])
    return alts[0]["message"]["text"] if alts else ""


# ---------------------------------------------------------------- GigaChat
_GIGA_TOKENS: Dict[str, Any] = {}


def _gigachat(conn, model, prompt, system, images, max_tokens, temperature) -> str:
    import httpx

    if images:
        raise LLMError("Изображения в GigaChat через этот блок не поддерживаются")
    key = conn.get("api_key") or ""
    verify = conn.get("verify", True)
    cached = _GIGA_TOKENS.get(key)
    if not cached or cached[1] < time.time() + 60:
        scope = conn.get("folder_id") or "GIGACHAT_API_PERS"  # для юрлиц: GIGACHAT_API_CORP
        r = httpx.post("https://ngw.devices.sberbank.ru:9443/api/v2/oauth", data={"scope": scope},
                       headers={"Authorization": f"Basic {key}", "RqUID": str(uuid.uuid4()),
                                "Accept": "application/json"}, timeout=60, verify=verify)
        if r.status_code >= 400:
            raise LLMError(f"GigaChat авторизация {r.status_code}: {r.text[:300]} "
                           "(для сертификатов Минцифры укажите verify=false или установите корневой сертификат)")
        data = r.json()
        cached = (data["access_token"], data.get("expires_at", 0) / 1000)
        _GIGA_TOKENS[key] = cached
    base = (conn.get("base_url") or "https://gigachat.devices.sberbank.ru/api/v1").rstrip("/")
    return _openai_call(base, cached[0], model, prompt, system, [], max_tokens, temperature, verify, "GigaChat")


# ---------------------------------------------------------------- OpenAI-совместимые
def _openai(conn, model, prompt, system, images, max_tokens, temperature) -> str:
    base = (conn.get("base_url") or "http://localhost:11434/v1").rstrip("/")
    return _openai_call(base, conn.get("api_key") or "", model, prompt, system, images, max_tokens, temperature,
                        conn.get("verify", True), "Модель")


def _openai_call(base, token, model, prompt, system, images, max_tokens, temperature, verify, label) -> str:
    import httpx

    if images:
        user: Any = [{"type": "image_url", "image_url": {"url": f"data:{_mime(i)};base64,{base64.b64encode(i).decode()}"}}
                     for i in images] + [{"type": "text", "text": prompt}]
    else:
        user = prompt
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": user}]
    body: Dict[str, Any] = {"model": model, "messages": messages, "max_tokens": max_tokens}
    if temperature is not None:
        body["temperature"] = temperature
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = httpx.post(f"{base}/chat/completions", json=body, headers=headers, timeout=300, verify=verify)
    if r.status_code >= 400:
        raise LLMError(f"{label} {r.status_code}: {r.text[:400]}")
    return r.json()["choices"][0]["message"]["content"] or ""


def _mime(data: bytes) -> str:
    if data[:4] == b"\x89PNG":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:4] == b"%PDF":
        return "application/pdf"
    return "image/png"


def extract_json(text: str) -> Any:
    """Достаёт JSON из ответа модели (в том числе из блока ```json … ```)."""
    m = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    candidate = (m.group(1) if m else text).strip()
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = candidate.find(opener), candidate.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(candidate[start:end + 1])
            except ValueError:
                continue
    raise LLMError("Модель вернула не JSON: " + text[:300])

"""Объектные хранилища S3: Yandex Object Storage (по умолчанию), MinIO, VK Cloud, AWS…"""
from __future__ import annotations

import mimetypes
import os
from typing import Any, Dict

from ..context import Context
from ..registry import Output, Param, block
from ..s3 import S3, YANDEX_ENDPOINT

P = Dict[str, Any]
CAT = "Яндекс · Хранилища"

CONN = [Param("access_key", "Идентификатор ключа", required=True, help="Статический ключ сервисного аккаунта"),
        Param("secret_key", "Секретный ключ (секрет)", "secret", required=True, default="yandex_s3_secret"),
        Param("endpoint", "Адрес хранилища", default=YANDEX_ENDPOINT, help="MinIO/VK Cloud/другое S3 — свой адрес"),
        Param("region", "Регион", default="ru-central1"),
        Param("bucket", "Бакет", required=True)]


def _s3(p: P) -> S3:
    return S3(p["access_key"], p["secret_key"], p["endpoint"] or YANDEX_ENDPOINT, p["region"] or "ru-central1")


@block("s3.upload", "Object Storage: загрузить", CAT,
       params=[*CONN, Param("file", "Локальный файл", required=True),
               Param("key", "Путь в бакете", help="Пусто — имя файла"),
               Param("result", "Ключ объекта в", "variable", default="s3_key")])
def upload(ctx: Context, p: P) -> None:
    path = os.path.expanduser(p["file"])
    key = p["key"] or os.path.basename(path)
    with open(path, "rb") as f:
        _s3(p).put(p["bucket"], key, f.read(), mimetypes.guess_type(path)[0] or "application/octet-stream")
    if p["result"]:
        ctx.vars[p["result"]] = key
    ctx.log(f"Загружено в {p['bucket']}/{key}")


@block("s3.download", "Object Storage: скачать", CAT,
       params=[*CONN, Param("key", "Путь в бакете", required=True), Param("file", "Сохранить как", required=True)])
def download(ctx: Context, p: P) -> None:
    data = _s3(p).get(p["bucket"], p["key"])
    path = os.path.expanduser(p["file"])
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


@block("s3.list", "Object Storage: список", CAT,
       params=[*CONN, Param("prefix", "Префикс (папка)"), Param("limit", "Не больше", "number", default=1000),
               Param("result", "Объекты в", "variable", required=True, default="objects")])
def list_(ctx: Context, p: P) -> None:
    ctx.vars[p["result"]] = _s3(p).list(p["bucket"], p["prefix"] or "", int(p["limit"] or 1000))


@block("s3.delete", "Object Storage: удалить", CAT, params=[*CONN, Param("key", "Путь в бакете", required=True)])
def delete(ctx: Context, p: P) -> None:
    _s3(p).delete(p["bucket"], p["key"])


@block("s3.link", "Object Storage: временная ссылка", CAT,
       params=[*CONN, Param("key", "Путь в бакете", required=True),
               Param("expires", "Действует, сек", "number", default=86400),
               Param("result", "Ссылка в", "variable", required=True, default="link")])
def link(ctx: Context, p: P) -> None:
    ctx.vars[p["result"]] = _s3(p).presign(p["bucket"], p["key"], int(p["expires"] or 86400))

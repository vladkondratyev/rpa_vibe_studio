"""Почта: чтение по IMAP (в том числе Exchange с включённым IMAP), отправка по SMTP."""
from __future__ import annotations

import email
import email.policy
import imaplib
import mimetypes
import os
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import getaddresses, parsedate_to_datetime
from typing import Any, Dict, List

from ..context import Context
from ..registry import Param, block

P = Dict[str, Any]
CAT = "Почта"


def _list(value: Any) -> List[str]:
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if v]
    return [v.strip() for v in re.split(r"[,;]", str(value)) if v.strip()]


def parse_message(raw: bytes, attachments_dir: str = "") -> Dict[str, Any]:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    text, html, files = "", "", []
    for part in msg.walk():
        if part.is_multipart():
            continue
        filename = part.get_filename()
        disp = part.get_content_disposition()
        if filename or disp == "attachment":
            name = _safe_name(filename or "attachment.bin")
            entry: Dict[str, Any] = {"name": name, "content_type": part.get_content_type(),
                                     "size": len(part.get_payload(decode=True) or b"")}
            if attachments_dir:
                os.makedirs(attachments_dir, exist_ok=True)
                path = _unique(os.path.join(attachments_dir, name))
                with open(path, "wb") as f:
                    f.write(part.get_payload(decode=True) or b"")
                entry["path"] = path
            files.append(entry)
        elif part.get_content_type() == "text/plain" and not text:
            text = part.get_content()
        elif part.get_content_type() == "text/html" and not html:
            html = part.get_content()
    if not text and html:
        text = re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<(script|style).*?</\1>", "", html))
        text = re.sub(r"[ \t]+", " ", text).strip()
    try:
        date = parsedate_to_datetime(msg["Date"]).isoformat() if msg["Date"] else None
    except (TypeError, ValueError):
        date = None
    return {
        "message_id": msg["Message-ID"], "subject": str(msg["Subject"] or ""),
        "from": ", ".join(a for _, a in getaddresses([str(msg["From"] or "")])),
        "from_name": ", ".join(n for n, _ in getaddresses([str(msg["From"] or "")])),
        "to": [a for _, a in getaddresses([str(msg["To"] or "")])],
        "date": date, "text": text, "html": html, "attachments": files,
    }


def _safe_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|\r\n]+', "_", name).strip() or "attachment.bin"


def _unique(path: str) -> str:
    base, ext = os.path.splitext(path)
    n = 1
    while os.path.exists(path):
        path = f"{base} ({n}){ext}"
        n += 1
    return path


@block(
    "email.read", "Прочитать почту", CAT,
    params=[
        Param("host", "IMAP-сервер", required=True), Param("port", "Порт", "number", default=993),
        Param("username", "Логин", required=True), Param("password", "Пароль (секрет)", "secret", required=True),
        Param("folder", "Папка", default="INBOX"),
        Param("criteria", "Критерий поиска", default="UNSEEN",
              help='IMAP: UNSEEN, ALL, FROM "fns.ru", SUBJECT "выписка", SINCE 01-Nov-2026'),
        Param("limit", "Не больше писем", "number", default=50),
        Param("attachments_dir", "Сохранять вложения в папку"),
        Param("mark_read", "Пометить прочитанными", "boolean", default=True),
        Param("move_to", "Переместить в папку"),
        Param("result", "Письма в", "variable", required=True, default="emails"),
    ],
    description="Список писем {subject, from, date, text, html, attachments[{name, path}]}.",
)
def read(ctx: Context, p: P) -> None:
    port = int(p["port"] or 993)
    imap = imaplib.IMAP4_SSL(p["host"], port) if port == 993 else imaplib.IMAP4(p["host"], port)
    if port != 993:
        try:
            imap.starttls(ssl.create_default_context())
        except imaplib.IMAP4.error:
            pass
    try:
        imap.login(p["username"], p["password"])
        imap.select(_quote_folder(p["folder"] or "INBOX"))
        criteria = p["criteria"] or "ALL"
        if criteria.isascii():
            typ, data = imap.uid("SEARCH", None, criteria)
        else:  # кириллица в критерии: CHARSET UTF-8 (imaplib по умолчанию шлёт только ASCII)
            imap._encoding = "utf-8"  # type: ignore[attr-defined]
            typ, data = imap.uid("SEARCH", "CHARSET", "UTF-8", criteria)
        uids = (data[0] or b"").split()[-int(p["limit"] or 50):]
        out = []
        for uid in uids:
            ctx.check_cancel()
            typ, msg_data = imap.uid("FETCH", uid, "(BODY.PEEK[])")
            raw = next((part[1] for part in msg_data if isinstance(part, tuple)), b"")
            item = parse_message(raw, os.path.expanduser(p["attachments_dir"]) if p["attachments_dir"] else "")
            item["uid"] = uid.decode()
            out.append(item)
            if p["mark_read"]:
                imap.uid("STORE", uid, "+FLAGS", "(\\Seen)")
            if p["move_to"]:
                imap.uid("COPY", uid, _quote_folder(p["move_to"]))
                imap.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
        if p["move_to"]:
            imap.expunge()
    finally:
        try:
            imap.logout()
        except Exception:
            pass
    ctx.vars[p["result"]] = out
    ctx.log(f"Прочитано писем: {len(out)}")


def _quote_folder(name: str) -> str:
    try:
        name.encode("ascii")
        return f'"{name}"' if " " in name else name
    except UnicodeEncodeError:
        return '"' + _imap_utf7(name) + '"'


def _imap_utf7(s: str) -> str:
    import base64

    out, buf = [], ""

    def flush() -> None:
        nonlocal buf
        if buf:
            b = base64.b64encode(buf.encode("utf-16-be")).decode().rstrip("=").replace("/", ",")
            out.append("&" + b + "-")
            buf = ""

    for ch in s:
        if 0x20 <= ord(ch) <= 0x7E:
            flush()
            out.append("&-" if ch == "&" else ch)
        else:
            buf += ch
    flush()
    return "".join(out)


@block(
    "email.send", "Отправить письмо", CAT,
    params=[
        Param("host", "SMTP-сервер", required=True), Param("port", "Порт", "number", default=587),
        Param("security", "Шифрование", "select", default="starttls", options=["starttls", "ssl", "none"]),
        Param("username", "Логин"), Param("password", "Пароль (секрет)", "secret"),
        Param("sender", "От кого", required=True), Param("to", "Кому", required=True, help="Через запятую"),
        Param("cc", "Копия"), Param("subject", "Тема", required=True),
        Param("body", "Текст", "text"), Param("html", "Текст — HTML", "boolean", default=False),
        Param("attachments", "Вложения", help="Путь или {{ список путей }}"),
    ],
)
def send(ctx: Context, p: P) -> None:
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = p["sender"], ", ".join(_list(p["to"])), p["subject"]
    if p["cc"]:
        msg["Cc"] = ", ".join(_list(p["cc"]))
    body = str(p["body"] or "")
    if p["html"]:
        msg.set_content(re.sub(r"<[^>]+>", "", body))
        msg.add_alternative(body, subtype="html")
    else:
        msg.set_content(body)
    for path in _list(p["attachments"]):
        path = os.path.expanduser(path)
        ctype, _ = mimetypes.guess_type(path)
        main, sub = (ctype or "application/octet-stream").split("/", 1)
        with open(path, "rb") as f:
            msg.add_attachment(f.read(), maintype=main, subtype=sub, filename=os.path.basename(path))
    port = int(p["port"] or 587)
    security = p["security"] or "starttls"
    smtp = smtplib.SMTP_SSL(p["host"], port, timeout=60) if security == "ssl" else smtplib.SMTP(p["host"], port, timeout=60)
    try:
        if security == "starttls":
            smtp.starttls(context=ssl.create_default_context())
        if p["username"]:
            smtp.login(p["username"], p["password"] or "")
        smtp.send_message(msg)
    finally:
        smtp.quit()
    ctx.log(f"Письмо «{p['subject']}» отправлено: {msg['To']}")

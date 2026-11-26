"""Пользователи, роли, токены, LDAP/Active Directory, аудит, шифрование секретов.

Роли (по возрастанию прав):
  viewer    — смотрит процессы, запуски, журналы, аналитику;
  operator  — запускает и останавливает процессы, работает с элементами очередей;
  developer — создаёт и меняет процессы, расписания, очереди, шаблоны, секреты;
  admin     — всё, включая пользователей, подключения ИИ и аудит;
  robot     — только API роботов (/api/agent/*).

RPA_TOKEN по-прежнему работает: это «главный ключ» с правами admin и robot.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import logging
import os
import secrets as pysecrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import ApiToken, AuditLog, User, get_session, utcnow

log = logging.getLogger("rpa.security")
MASTER_TOKEN = os.environ.get("RPA_TOKEN", "")
ROLES = ["viewer", "operator", "developer", "admin"]
ALL_ROLES = ROLES + ["robot"]
SESSION_HOURS = int(os.environ.get("RPA_SESSION_HOURS", "12"))


@dataclass
class Principal:
    username: str
    role: str
    user_id: Optional[int] = None

    def can(self, role: str) -> bool:
        if role == "robot":
            return self.role in ("robot", "admin")
        if self.role == "robot":
            return False
        return ROLES.index(self.role) >= ROLES.index(role)


# ---------------------------------------------------------------- пароли и токены
def hash_password(password: str) -> str:
    salt = pysecrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 240_000)
    return "pbkdf2$240000$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iters, salt, digest = stored.split("$")
        calc = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt), int(iters))
        return hmac.compare_digest(calc, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_token(db: Session, user: User, kind: str = "session", name: str = "",
                hours: Optional[int] = SESSION_HOURS) -> str:
    raw = "rvs_" + pysecrets.token_urlsafe(32)
    db.add(ApiToken(user_id=user.id, token_hash=token_hash(raw), kind=kind, name=name,
                    expires_at=utcnow() + dt.timedelta(hours=hours) if hours else None))
    db.commit()
    return raw


def auth_enabled(db: Session) -> bool:
    return bool(MASTER_TOKEN) or db.execute(select(User.id).limit(1)).first() is not None


def principal_from_token(db: Session, raw: str) -> Optional[Principal]:
    if MASTER_TOKEN and hmac.compare_digest(raw.encode(), MASTER_TOKEN.encode()):
        return Principal("admin (RPA_TOKEN)", "admin")
    if not raw:
        return None
    tok = db.execute(select(ApiToken).where(ApiToken.token_hash == token_hash(raw))).scalar_one_or_none()
    if tok is None or (tok.expires_at and tok.expires_at < utcnow()):
        return None
    user = db.get(User, tok.user_id)
    if user is None or not user.active:
        return None
    now = utcnow()
    if not tok.last_used or (now - tok.last_used).total_seconds() > 60:
        tok.last_used = now
        db.commit()
    return Principal(user.username, user.role, user.id)


def current(authorization: str = Header(default=""), db: Session = Depends(get_session)) -> Principal:
    raw = authorization[7:] if authorization.startswith("Bearer ") else ""
    if not auth_enabled(db):
        return Principal("dev", "admin")  # режим разработки: ни токена, ни пользователей
    p = principal_from_token(db, raw)
    if p is None:
        raise HTTPException(401, "Требуется вход")
    return p


def need(role: str) -> Callable[..., Principal]:
    def dep(p: Principal = Depends(current)) -> Principal:
        if not p.can(role):
            raise HTTPException(403, f"Недостаточно прав: нужна роль «{role}»")
        return p

    return dep


VIEW, OPERATE, DEVELOP, ADMIN, ROBOT = (need(r) for r in ("viewer", "operator", "developer", "admin", "robot"))


def audit(db: Session, who: Principal, action: str, entity: str = "", entity_id: Any = "",
          details: Optional[Dict[str, Any]] = None, commit: bool = True) -> None:
    db.add(AuditLog(username=who.username, action=action, entity=entity, entity_id=str(entity_id or ""),
                    details=details or {}))
    if commit:
        db.commit()


# ---------------------------------------------------------------- LDAP / Active Directory
def ldap_login(username: str, password: str) -> Optional[Dict[str, str]]:
    """Проверка пароля в каталоге. Настройки:
      RPA_LDAP_URL=ldaps://dc.corp.local
      RPA_LDAP_USER=  шаблон для bind, например {username}@corp.local или uid={username},ou=people,dc=corp
      RPA_LDAP_BASE=  dc=corp,dc=local (для поиска групп и ФИО)
      RPA_LDAP_ROLES= CN=RPA-Admins,OU=Groups,DC=corp=admin;CN=RPA-Dev,...=developer
      RPA_LDAP_DEFAULT_ROLE=viewer
    """
    url = os.environ.get("RPA_LDAP_URL")
    if not url or not password:
        return None
    try:
        import ldap3
    except ImportError:
        log.error("RPA_LDAP_URL задан, но пакет ldap3 не установлен")
        return None
    bind_user = os.environ.get("RPA_LDAP_USER", "{username}").format(username=username)
    try:
        conn = ldap3.Connection(ldap3.Server(url, get_info=ldap3.NONE, connect_timeout=10), user=bind_user,
                                password=password, auto_bind=True, receive_timeout=15)
    except Exception as exc:
        log.info("LDAP: вход %s отклонён: %s", username, exc)
        return None
    role = os.environ.get("RPA_LDAP_DEFAULT_ROLE", "viewer")
    full_name = ""
    base = os.environ.get("RPA_LDAP_BASE")
    if base:
        flt = f"(|(sAMAccountName={_ldap_escape(username)})(uid={_ldap_escape(username)})" \
              f"(userPrincipalName={_ldap_escape(bind_user)}))"
        conn.search(base, flt, attributes=["memberOf", "displayName", "cn"])
        if conn.entries:
            entry = conn.entries[0]
            full_name = str(entry.displayName or entry.cn or "") if hasattr(entry, "displayName") else ""
            groups = {str(g).lower() for g in (entry.memberOf.values if "memberOf" in entry else [])}
            mapping = [part.rsplit("=", 1) for part in os.environ.get("RPA_LDAP_ROLES", "").split(";") if "=" in part]
            best = -1
            for group, r in mapping:
                if group.strip().lower() in groups and r.strip() in ROLES and ROLES.index(r.strip()) > best:
                    best, role = ROLES.index(r.strip()), r.strip()
    conn.unbind()
    return {"role": role, "full_name": full_name}


def _ldap_escape(s: str) -> str:
    return "".join(f"\\{ord(c):02x}" if c in "*()\\\0" else c for c in s)


# ---------------------------------------------------------------- шифрование секретов
_fernet = None


def _key_material() -> bytes:
    env = os.environ.get("RPA_SECRET_KEY")
    if env:
        return env.encode()
    path = Path(os.environ.get("RPA_SECRET_KEY_FILE", "rpa_secret.key"))
    if path.exists():
        return path.read_bytes().strip()
    key = pysecrets.token_urlsafe(48).encode()
    path.write_bytes(key)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    log.warning("Создан ключ шифрования секретов %s — сохраните его резервную копию", path.resolve())
    return key


def fernet() -> Any:
    global _fernet
    if _fernet is None:
        from cryptography.fernet import Fernet

        digest = hashlib.sha256(b"rpa-vibe-studio:" + _key_material()).digest()
        _fernet = Fernet(base64.urlsafe_b64encode(digest))
    return _fernet


def encrypt(value: str) -> str:
    return fernet().encrypt(value.encode()).decode()


def decrypt(token: str) -> str:
    return fernet().decrypt(token.encode()).decode()

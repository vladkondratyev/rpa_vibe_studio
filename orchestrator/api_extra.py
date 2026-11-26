"""API версии 0.2: вход и пользователи, аудит, секреты, очереди, модели ИИ,
OCR-шаблоны, галерея шаблонов процессов, генерация процесса по описанию,
аналитика, версии, импорт/экспорт."""
from __future__ import annotations

import base64
import datetime as dt
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from rpa_engine import catalog, llm, ocr, validate

from . import services
from .models import (
    AIConnection, ApiToken, AuditLog, Job, OcrTemplate, Process, ProcessVersion, QueueItem, Robot, Secret, User,
    WorkQueue, get_session, utcnow,
)
from .security import (
    ADMIN, ALL_ROLES, DEVELOP, OPERATE, ROBOT, VIEW, Principal, audit, current, decrypt, encrypt, hash_password,
    issue_token, ldap_login, token_hash, verify_password,
)

router = APIRouter()
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


def iso(v: Optional[dt.datetime]) -> Optional[str]:
    return v.isoformat() + "Z" if v else None


def get_or_404(db: Session, model: Any, id: int) -> Any:
    obj = db.get(model, id)
    if obj is None:
        raise HTTPException(404, "Не найдено")
    return obj


# ================================================================ вход и пользователи
class LoginIn(BaseModel):
    username: str
    password: str


@router.post("/api/auth/login")
def login(body: LoginIn, db: Session = Depends(get_session)) -> Dict[str, Any]:
    user = db.execute(select(User).where(User.username == body.username)).scalar_one_or_none()
    ok = False
    if user and user.active and user.source == "local" and user.password_hash:
        ok = verify_password(body.password, user.password_hash)
    elif (user is None or user.source == "ldap") and (info := ldap_login(body.username, body.password)):
        if user is None:
            user = User(username=body.username, source="ldap", role=info["role"])
            db.add(user)
        user.full_name = info.get("full_name") or user.full_name
        user.role = info["role"] if user.role != "admin" else user.role
        ok = user.active is not False
        db.commit()
    if not ok or user is None:
        db.add(AuditLog(username=body.username, action="auth.failed", entity="user"))
        db.commit()
        raise HTTPException(401, "Неверный логин или пароль")
    user.last_login = utcnow()
    token = issue_token(db, user, "session", "вход в интерфейс")
    db.add(AuditLog(username=user.username, action="auth.login", entity="user", entity_id=str(user.id)))
    db.commit()
    return {"token": token, "user": user_out(user)}


@router.get("/api/auth/me")
def me(who: Principal = Depends(current)) -> Dict[str, Any]:
    return {"username": who.username, "role": who.role, "user_id": who.user_id}


class UserIn(BaseModel):
    username: str = Field(min_length=1, max_length=200)
    full_name: str = ""
    role: str = "viewer"
    password: Optional[str] = None
    active: bool = True


def user_out(u: User) -> Dict[str, Any]:
    return {"id": u.id, "username": u.username, "full_name": u.full_name, "role": u.role, "source": u.source,
            "active": u.active, "created_at": iso(u.created_at), "last_login": iso(u.last_login)}


@router.get("/api/users", dependencies=[Depends(ADMIN)])
def list_users(db: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    return [user_out(u) for u in db.execute(select(User).order_by(User.username)).scalars()]


@router.post("/api/users", status_code=201)
def create_user(body: UserIn, db: Session = Depends(get_session), who: Principal = Depends(ADMIN)) -> Dict[str, Any]:
    if body.role not in ALL_ROLES:
        raise HTTPException(422, "Неизвестная роль")
    if db.execute(select(User).where(User.username == body.username)).scalar_one_or_none():
        raise HTTPException(409, "Пользователь уже есть")
    u = User(username=body.username, full_name=body.full_name, role=body.role, active=body.active,
             password_hash=hash_password(body.password) if body.password else "")
    db.add(u)
    db.commit()
    audit(db, who, "user.create", "user", u.id, {"username": u.username, "role": u.role})
    return user_out(u)


@router.put("/api/users/{uid}")
def update_user(uid: int, body: UserIn, db: Session = Depends(get_session),
                who: Principal = Depends(ADMIN)) -> Dict[str, Any]:
    u = get_or_404(db, User, uid)
    if body.role not in ALL_ROLES:
        raise HTTPException(422, "Неизвестная роль")
    u.full_name, u.role, u.active = body.full_name, body.role, body.active
    if body.password:
        u.password_hash = hash_password(body.password)
    if not u.active:
        db.execute(delete(ApiToken).where(ApiToken.user_id == u.id))
    db.commit()
    audit(db, who, "user.update", "user", u.id, {"role": u.role, "active": u.active,
                                                  "password_changed": bool(body.password)})
    return user_out(u)


@router.delete("/api/users/{uid}", status_code=204)
def delete_user(uid: int, db: Session = Depends(get_session), who: Principal = Depends(ADMIN)) -> Response:
    u = get_or_404(db, User, uid)
    db.delete(u)
    db.commit()
    audit(db, who, "user.delete", "user", uid, {"username": u.username})
    return Response(status_code=204)


class TokenIn(BaseModel):
    name: str = "API"
    days: Optional[int] = None


@router.post("/api/users/{uid}/tokens", status_code=201)
def create_api_token(uid: int, body: TokenIn, db: Session = Depends(get_session),
                     who: Principal = Depends(ADMIN)) -> Dict[str, Any]:
    u = get_or_404(db, User, uid)
    raw = issue_token(db, u, "api", body.name, hours=body.days * 24 if body.days else None)
    audit(db, who, "token.create", "user", uid, {"name": body.name})
    return {"token": raw}


# ================================================================ аудит
@router.get("/api/audit", dependencies=[Depends(ADMIN)])
def audit_log(limit: int = Query(300, le=2000), action: Optional[str] = None, user: Optional[str] = None,
              db: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    q = select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)
    if action:
        q = q.where(AuditLog.action.like(f"{action}%"))
    if user:
        q = q.where(AuditLog.username == user)
    return [{"id": a.id, "ts": iso(a.ts), "username": a.username, "action": a.action, "entity": a.entity,
             "entity_id": a.entity_id, "details": a.details} for a in db.execute(q).scalars()]


# ================================================================ секреты
class SecretIn(BaseModel):
    name: str = Field(min_length=1, max_length=200, pattern=r"^[\w.\-]+$")
    value: Optional[str] = None
    description: str = ""


def secret_out(s: Secret) -> Dict[str, Any]:
    return {"id": s.id, "name": s.name, "description": s.description, "updated_by": s.updated_by,
            "updated_at": iso(s.updated_at)}


@router.get("/api/secrets", dependencies=[Depends(DEVELOP)])
def list_secrets(db: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    return [secret_out(s) for s in db.execute(select(Secret).order_by(Secret.name)).scalars()]


@router.put("/api/secrets/{name}")
def put_secret(name: str, body: SecretIn, db: Session = Depends(get_session),
               who: Principal = Depends(DEVELOP)) -> Dict[str, Any]:
    s = db.execute(select(Secret).where(Secret.name == name)).scalar_one_or_none()
    if s is None:
        if not body.value:
            raise HTTPException(422, "Укажите значение секрета")
        s = Secret(name=body.name, value_enc=encrypt(body.value))
        db.add(s)
    elif body.value:
        s.value_enc = encrypt(body.value)
    s.name, s.description, s.updated_by, s.updated_at = body.name, body.description, who.username, utcnow()
    db.commit()
    audit(db, who, "secret.write", "secret", s.id, {"name": s.name, "value_changed": bool(body.value)})
    return secret_out(s)


@router.delete("/api/secrets/{name}", status_code=204)
def delete_secret(name: str, db: Session = Depends(get_session), who: Principal = Depends(DEVELOP)) -> Response:
    s = db.execute(select(Secret).where(Secret.name == name)).scalar_one_or_none()
    if s is None:
        raise HTTPException(404, "Не найдено")
    db.delete(s)
    db.commit()
    audit(db, who, "secret.delete", "secret", s.id, {"name": name})
    return Response(status_code=204)


# ================================================================ очереди
class QueueIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    max_retries: int = 2
    sla_minutes: int = 0
    process_id: Optional[int] = None
    max_parallel: int = 1
    robot_tags: List[str] = []


class ItemsIn(BaseModel):
    items: List[Dict[str, Any]]


class ItemPatch(BaseModel):
    data: Optional[Dict[str, Any]] = None
    status: Optional[str] = None
    priority: Optional[int] = None


def queue_out(q: WorkQueue, db: Session) -> Dict[str, Any]:
    counts = dict(db.execute(select(QueueItem.status, func.count()).where(QueueItem.queue_id == q.id)
                             .group_by(QueueItem.status)).all())
    overdue = db.scalar(select(func.count()).select_from(QueueItem).where(
        QueueItem.queue_id == q.id, QueueItem.status.in_(("new", "in_progress")),
        QueueItem.deadline.isnot(None), QueueItem.deadline < utcnow())) or 0
    p = db.get(Process, q.process_id) if q.process_id else None
    return {"id": q.id, "name": q.name, "description": q.description, "max_retries": q.max_retries,
            "sla_minutes": q.sla_minutes, "process_id": q.process_id, "process_name": p.name if p else None,
            "max_parallel": q.max_parallel, "robot_tags": q.robot_tags or [],
            "counts": {k: counts.get(k, 0) for k in ("new", "in_progress", "done", "failed")}, "overdue": overdue}


def item_out(i: QueueItem) -> Dict[str, Any]:
    return {"id": i.id, "queue_id": i.queue_id, "reference": i.reference, "data": i.data, "priority": i.priority,
            "status": i.status, "retries": i.retries, "result": i.result, "error": i.error,
            "business_error": i.business_error, "locked_by": i.locked_by, "job_id": i.job_id,
            "created_at": iso(i.created_at), "started_at": iso(i.started_at), "finished_at": iso(i.finished_at),
            "deadline": iso(i.deadline),
            "overdue": bool(i.deadline and i.status in ("new", "in_progress") and i.deadline < utcnow())}


@router.get("/api/queues", dependencies=[Depends(VIEW)])
def list_queues(db: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    return [queue_out(q, db) for q in db.execute(select(WorkQueue).order_by(WorkQueue.name)).scalars()]


def _apply_queue(q: WorkQueue, body: QueueIn) -> None:
    q.name, q.description, q.max_retries = body.name, body.description, max(0, body.max_retries)
    q.sla_minutes, q.process_id = max(0, body.sla_minutes), body.process_id
    q.max_parallel, q.robot_tags = max(1, body.max_parallel), sorted(set(body.robot_tags))


@router.post("/api/queues", status_code=201)
def create_queue(body: QueueIn, db: Session = Depends(get_session), who: Principal = Depends(DEVELOP)) -> Dict[str, Any]:
    if db.execute(select(WorkQueue).where(WorkQueue.name == body.name)).scalar_one_or_none():
        raise HTTPException(409, "Очередь с таким именем уже есть")
    q = WorkQueue()
    _apply_queue(q, body)
    db.add(q)
    db.commit()
    audit(db, who, "queue.create", "queue", q.id, {"name": q.name})
    return queue_out(q, db)


@router.put("/api/queues/{qid}")
def update_queue(qid: int, body: QueueIn, db: Session = Depends(get_session),
                 who: Principal = Depends(DEVELOP)) -> Dict[str, Any]:
    q = get_or_404(db, WorkQueue, qid)
    _apply_queue(q, body)
    db.commit()
    audit(db, who, "queue.update", "queue", q.id)
    return queue_out(q, db)


@router.delete("/api/queues/{qid}", status_code=204)
def delete_queue(qid: int, db: Session = Depends(get_session), who: Principal = Depends(DEVELOP)) -> Response:
    q = get_or_404(db, WorkQueue, qid)
    db.execute(delete(QueueItem).where(QueueItem.queue_id == qid))
    db.delete(q)
    db.commit()
    audit(db, who, "queue.delete", "queue", qid, {"name": q.name})
    return Response(status_code=204)


@router.get("/api/queues/{qid}/items", dependencies=[Depends(VIEW)])
def list_items(qid: int, status: Optional[str] = None, limit: int = Query(200, le=2000),
               db: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    q = select(QueueItem).where(QueueItem.queue_id == qid).order_by(QueueItem.id.desc()).limit(limit)
    if status:
        q = q.where(QueueItem.status == status)
    return [item_out(i) for i in db.execute(q).scalars()]


@router.post("/api/queues/{qid}/items", status_code=201)
def add_queue_items(qid: int, body: ItemsIn, db: Session = Depends(get_session),
                    who: Principal = Depends(OPERATE)) -> Dict[str, Any]:
    q = get_or_404(db, WorkQueue, qid)
    added = services.add_items(db, q, body.items)
    audit(db, who, "queue.items_add", "queue", qid, {"added": added})
    return {"added": added}


@router.patch("/api/queue-items/{iid}")
def patch_item(iid: int, body: ItemPatch, db: Session = Depends(get_session),
               who: Principal = Depends(OPERATE)) -> Dict[str, Any]:
    i = get_or_404(db, QueueItem, iid)
    if body.data is not None:
        i.data = body.data
    if body.priority is not None:
        i.priority = body.priority
    if body.status:
        if body.status not in ("new", "done", "failed"):
            raise HTTPException(422, "Допустимые статусы: new, done, failed")
        i.status = body.status
        if body.status == "new":
            i.locked_by, i.job_id, i.error, i.started_at, i.finished_at = None, None, None, None, None
        else:
            i.finished_at = utcnow()
    db.commit()
    audit(db, who, "queue.item_update", "queue_item", iid, {"status": body.status, "data_changed": body.data is not None})
    return item_out(i)


# ---------------------------------------------------------------- очереди: API роботов
class AgentItemsIn(BaseModel):
    items: List[Dict[str, Any]]


class AgentNextIn(BaseModel):
    robot: str
    job_id: Optional[int] = None


class AgentItemFinishIn(BaseModel):
    status: str
    result: Any = None
    error: Optional[str] = None
    business: bool = False


@router.post("/api/agent/queues/{name}/items", dependencies=[Depends(ROBOT)])
def agent_add_items(name: str, body: AgentItemsIn, db: Session = Depends(get_session)) -> Dict[str, Any]:
    try:
        q = services.queue_by_name(db, name)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from None
    return {"added": services.add_items(db, q, body.items)}


@router.post("/api/agent/queues/{name}/next", dependencies=[Depends(ROBOT)])
def agent_next_item(name: str, body: AgentNextIn, db: Session = Depends(get_session)) -> Response:
    try:
        q = services.queue_by_name(db, name)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from None
    item = services.next_item(db, q, body.robot, body.job_id)
    if item is None:
        return Response(status_code=204)
    return Response(json.dumps(item_out(item), ensure_ascii=False, default=str), media_type="application/json")


@router.post("/api/agent/queue-items/{iid}/finish", dependencies=[Depends(ROBOT)])
def agent_finish_item(iid: int, body: AgentItemFinishIn, db: Session = Depends(get_session)) -> Dict[str, Any]:
    if body.status not in ("done", "failed"):
        raise HTTPException(422, "Статус: done или failed")
    item = get_or_404(db, QueueItem, iid)
    return item_out(services.finish_item(db, item, body.status, body.result, body.error, body.business))


class AgentRunIn(BaseModel):
    name: str
    inputs: Dict[str, Any] = {}
    parent_job: Optional[int] = None


@router.post("/api/agent/run", dependencies=[Depends(ROBOT)])
def agent_run(body: AgentRunIn, db: Session = Depends(get_session)) -> Dict[str, Any]:
    p = db.execute(select(Process).where(Process.name == body.name)).scalar_one_or_none()
    if p is None:
        raise HTTPException(404, f"Процесс «{body.name}» не найден")
    job = services.create_job(db, p, body.inputs, [], None, "process",
                              started_by=f"запуск #{body.parent_job}" if body.parent_job else "робот")
    return {"id": job.id, "status": job.status}


@router.get("/api/agent/jobs/{jid}", dependencies=[Depends(ROBOT)])
def agent_job_status(jid: int, db: Session = Depends(get_session)) -> Dict[str, Any]:
    j = get_or_404(db, Job, jid)
    return {"id": j.id, "status": j.status, "outputs": j.outputs, "error": j.error}


# ================================================================ модели ИИ
class AIIn(BaseModel):
    name: str = Field(min_length=1, max_length=100, pattern=r"^[\w.\-]+$")
    provider: str
    model: str = ""
    base_url: str = ""
    folder_id: str = ""
    secret_name: str = ""
    verify: bool = True
    is_default: bool = False
    description: str = ""


def ai_out(c: AIConnection) -> Dict[str, Any]:
    return {"id": c.id, "name": c.name, "provider": c.provider, "provider_title": llm.PROVIDERS.get(c.provider),
            "model": c.model or llm.DEFAULT_MODELS.get(c.provider, ""), "base_url": c.base_url,
            "folder_id": c.folder_id, "secret_name": c.secret_name, "verify": c.verify, "is_default": c.is_default,
            "description": c.description}


@router.get("/api/ai/providers", dependencies=[Depends(VIEW)])
def ai_providers() -> List[Dict[str, str]]:
    return [{"id": k, "title": v, "default_model": llm.DEFAULT_MODELS[k]} for k, v in llm.PROVIDERS.items()]


@router.get("/api/ai", dependencies=[Depends(VIEW)])
def list_ai(db: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    return [ai_out(c) for c in db.execute(select(AIConnection).order_by(AIConnection.name)).scalars()]


def _apply_ai(c: AIConnection, body: AIIn, db: Session) -> None:
    if body.provider not in llm.PROVIDERS:
        raise HTTPException(422, "Неизвестный провайдер")
    for k, v in body.model_dump().items():
        setattr(c, k, v)
    if body.is_default:
        for other in db.execute(select(AIConnection).where(AIConnection.is_default.is_(True))).scalars():
            if other is not c:
                other.is_default = False


@router.post("/api/ai", status_code=201)
def create_ai(body: AIIn, db: Session = Depends(get_session), who: Principal = Depends(ADMIN)) -> Dict[str, Any]:
    if db.execute(select(AIConnection).where(AIConnection.name == body.name)).scalar_one_or_none():
        raise HTTPException(409, "Подключение с таким именем уже есть")
    c = AIConnection()
    _apply_ai(c, body, db)
    db.add(c)
    db.commit()
    audit(db, who, "ai.create", "ai", c.id, {"name": c.name, "provider": c.provider})
    return ai_out(c)


@router.put("/api/ai/{cid}")
def update_ai(cid: int, body: AIIn, db: Session = Depends(get_session), who: Principal = Depends(ADMIN)) -> Dict[str, Any]:
    c = get_or_404(db, AIConnection, cid)
    _apply_ai(c, body, db)
    db.commit()
    audit(db, who, "ai.update", "ai", c.id)
    return ai_out(c)


@router.delete("/api/ai/{cid}", status_code=204)
def delete_ai(cid: int, db: Session = Depends(get_session), who: Principal = Depends(ADMIN)) -> Response:
    c = get_or_404(db, AIConnection, cid)
    db.delete(c)
    db.commit()
    audit(db, who, "ai.delete", "ai", cid, {"name": c.name})
    return Response(status_code=204)


def _conn(db: Session, name: Optional[str]) -> Dict[str, Any]:
    conns = services.ai_connections(db)
    if name:
        if name not in conns:
            raise HTTPException(404, f"Подключение «{name}» не найдено")
        return conns[name]
    default = next((c for c in conns.values() if c.get("is_default")), None) or next(iter(conns.values()), None)
    if default is None:
        default = llm.env_connection()
    if default is None:
        raise HTTPException(422, "Добавьте подключение к модели ИИ в разделе «Модели ИИ»")
    return default


@router.post("/api/ai/{cid}/test", dependencies=[Depends(ADMIN)])
def test_ai(cid: int, db: Session = Depends(get_session)) -> Dict[str, Any]:
    c = get_or_404(db, AIConnection, cid)
    try:
        answer = llm.chat(_conn(db, c.name), "Ответь одним словом: работает?", max_tokens=2000)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "answer": answer.strip()[:300]}


# ================================================================ OCR-шаблоны
class OcrTemplateIn(BaseModel):
    definition: Dict[str, Any]


@router.get("/api/ocr/templates", dependencies=[Depends(VIEW)])
def list_ocr_templates(db: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    custom = {t.slug: t for t in db.execute(select(OcrTemplate)).scalars()}
    out = []
    for t in ocr.BUILTIN_TEMPLATES:
        c = custom.pop(t["id"], None)
        out.append({"id": t["id"], "builtin": True, "customized": c is not None,
                    "definition": c.definition if c else t})
    for slug, c in sorted(custom.items()):
        out.append({"id": slug, "builtin": False, "customized": True, "definition": c.definition})
    return out


@router.put("/api/ocr/templates/{slug}")
def put_ocr_template(slug: str, body: OcrTemplateIn, db: Session = Depends(get_session),
                     who: Principal = Depends(DEVELOP)) -> Dict[str, Any]:
    if not re.fullmatch(r"[\w\-]+", slug):
        raise HTTPException(422, "id шаблона: латиница, цифры, _ и -")
    d = dict(body.definition)
    d["id"] = slug
    if not d.get("name") or not isinstance(d.get("fields"), list) or not d["fields"]:
        raise HTTPException(422, "В шаблоне нужны name и хотя бы одно поле в fields")
    for f in d["fields"]:
        if not f.get("name"):
            raise HTTPException(422, "У каждого поля должен быть name")
        if f.get("regex"):
            try:
                re.compile(f["regex"])
            except re.error as exc:
                raise HTTPException(422, f"Поле {f['name']}: ошибка в регулярном выражении: {exc}") from None
        if f.get("zone") and (len(f["zone"]) != 4 or not all(0 <= float(v) <= 1 for v in f["zone"])):
            raise HTTPException(422, f"Поле {f['name']}: зона — 4 числа от 0 до 1")
    t = db.execute(select(OcrTemplate).where(OcrTemplate.slug == slug)).scalar_one_or_none()
    if t is None:
        t = OcrTemplate(slug=slug, definition=d)
        db.add(t)
    t.definition, t.updated_by, t.updated_at = d, who.username, utcnow()
    db.commit()
    audit(db, who, "ocr_template.write", "ocr_template", slug)
    return {"id": slug, "definition": d}


@router.delete("/api/ocr/templates/{slug}", status_code=204)
def delete_ocr_template(slug: str, db: Session = Depends(get_session), who: Principal = Depends(DEVELOP)) -> Response:
    db.execute(delete(OcrTemplate).where(OcrTemplate.slug == slug))
    db.commit()
    audit(db, who, "ocr_template.delete", "ocr_template", slug)
    return Response(status_code=204)


class OcrTestIn(BaseModel):
    file_b64: str
    template: Optional[Dict[str, Any]] = None
    template_id: Optional[str] = None
    engine: str = "tesseract"
    connection: Optional[str] = None


@router.post("/api/ocr/test", dependencies=[Depends(DEVELOP)])
def ocr_test(body: OcrTestIn, db: Session = Depends(get_session)) -> Dict[str, Any]:
    """Пробное распознавание на сервере оркестратора — для настройки шаблона в интерфейсе."""
    raw = base64.b64decode(body.file_b64.split(",", 1)[-1])
    try:
        pages, text_layer = ocr.load_pages(raw, max_pages=2)
        kw: Dict[str, Any] = {}
        if body.engine == "yandex":
            kw["yandex_conn"] = _conn(db, body.connection)
        if body.engine == "ai":
            kw["ai_conn"] = _conn(db, body.connection)
        tpl = body.template
        if tpl is None and body.template_id:
            tpl = next((t["definition"] for t in list_ocr_templates(db) if t["id"] == body.template_id), None)
        ymodel = (tpl or {}).get("yandex_model") or "page"
        results = [ocr.recognize(p, body.engine, yandex_model=ymodel, **kw) for p in pages] or \
            [{"text": text_layer, "words": [], "entities": {}}]
    except Exception as exc:
        raise HTTPException(422, str(exc)) from None
    out: Dict[str, Any] = {"text": "\n".join(r["text"] for r in results), "words": results[0]["words"][:3000],
                           "entities": results[0].get("entities") or {}}
    if tpl:
        out["extracted"] = ocr.extract(results, tpl)
    else:
        found, score = ocr.classify(out["text"], [t["definition"] for t in list_ocr_templates(db)])
        out["classified"] = {"id": found["id"], "score": score} if found else None
    return out


# ================================================================ галерея шаблонов процессов
def load_templates() -> List[Dict[str, Any]]:
    out = []
    for path in sorted(TEMPLATES_DIR.glob("*/*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        data["id"] = f"{path.parent.name}/{path.stem}"
        out.append(data)
    return out


@router.get("/api/templates", dependencies=[Depends(VIEW)])
def list_templates() -> List[Dict[str, Any]]:
    res = []
    for t in load_templates():
        res.append({k: v for k, v in t.items() if k != "definition"} |
                   {"blocks": len(t["definition"].get("nodes", [])),
                    "valid": not validate(t["definition"])})
    return res


@router.get("/api/templates/{group}/{name}", dependencies=[Depends(VIEW)])
def get_template(group: str, name: str) -> Dict[str, Any]:
    t = next((t for t in load_templates() if t["id"] == f"{group}/{name}"), None)
    if t is None:
        raise HTTPException(404, "Шаблон не найден")
    return t


class UseTemplateIn(BaseModel):
    name: Optional[str] = None


@router.post("/api/templates/{group}/{name}/use", status_code=201)
def use_template(group: str, name: str, body: UseTemplateIn, db: Session = Depends(get_session),
                 who: Principal = Depends(DEVELOP)) -> Dict[str, Any]:
    t = get_template(group, name)
    pname = _free_name(db, body.name or t["name"])
    p = Process(name=pname, description=t.get("description", ""), definition=t["definition"],
                minutes_saved=int(t.get("minutes_saved") or 0))
    db.add(p)
    db.commit()
    db.add(ProcessVersion(process_id=p.id, version=1, definition=p.definition, author=who.username))
    audit(db, who, "process.from_template", "process", p.id, {"template": t["id"]})
    return {"id": p.id, "name": p.name}


def _free_name(db: Session, name: str) -> str:
    candidate, n = name, 2
    while db.execute(select(Process.id).where(Process.name == candidate)).first():
        candidate, n = f"{name} ({n})", n + 1
    return candidate


# ================================================================ процесс по описанию (Vibe)
class VibeIn(BaseModel):
    prompt: str = Field(min_length=5)
    connection: Optional[str] = None
    create: bool = True


VIBE_SYSTEM = """Ты — архитектор RPA-процессов платформы RPA Vibe Studio. По описанию задачи на русском языке
построй процесс из блоков каталога. Верни только JSON вида:
{"name": "...", "description": "...", "minutes_saved": 15,
 "definition": {"variables": [{"name": "...", "default": ...}], "outputs": ["..."],
   "nodes": [{"id": "start", "type": "core.start", "label": "...", "params": {}}],
   "edges": [{"source": "start", "sourceHandle": "out", "target": "..."}]}}
Правила:
- ровно один core.start, процесс заканчивается core.end;
- sourceHandle — имя выхода блока (out, true/false, body/done, found/not_found, ok/review и т.д.), у любого блока
  с ошибкой можно подключить выход "error";
- тело цикла core.for_each начинается с выхода body; конец тела можно не соединять — он вернётся в цикл;
- параметры: строки поддерживают {{ выражения }}, параметры kind=expression — Python-выражение без скобок {{ }};
  kind=secret — ИМЯ секрета (пароли не пиши в процесс);
- заполняй все обязательные параметры правдоподобными значениями-заглушками, понятными пользователю;
- давай блокам короткие понятные label на русском;
- не придумывай типы блоков, которых нет в каталоге."""


def _catalog_brief() -> str:
    lines = []
    for b in catalog():
        params = ", ".join(f"{p['name']}:{p['kind']}{'*' if p['required'] else ''}" for p in b["params"])
        outs = "/".join(o["name"] for o in b["outputs"])
        lines.append(f"{b['type']} — {b['title']} ({params}) → {outs or '—'}")
    return "\n".join(lines)


def auto_layout(definition: Dict[str, Any]) -> None:
    nodes = {n["id"]: n for n in definition.get("nodes") or []}
    start = next((n["id"] for n in nodes.values() if n.get("type") == "core.start"), None)
    level: Dict[str, int] = {}
    order: Dict[int, int] = {}
    queue = [(start, 0)] if start else []
    while queue:
        nid, lv = queue.pop(0)
        if nid in level or nid not in nodes:
            continue
        level[nid] = lv
        for e in definition.get("edges") or []:
            if e.get("source") == nid:
                queue.append((e.get("target"), lv + 1))
    for nid, n in nodes.items():
        lv = level.get(nid, max(level.values(), default=0) + 1)
        row = order.get(lv, 0)
        order[lv] = row + 1
        n["position"] = {"x": 60 + lv * 330, "y": 60 + row * 170}


def generate_process(conn: Dict[str, Any], prompt: str) -> Dict[str, Any]:
    system = VIBE_SYSTEM + "\n\nКаталог блоков (тип — название (параметры, * обязательный) → выходы):\n" + _catalog_brief()
    text = llm.chat(conn, f"Задача: {prompt}", system=system, max_tokens=16000)
    data = llm.extract_json(text)
    errors = validate(data.get("definition") or {})
    if errors:
        fix = (f"Задача: {prompt}\n\nТвой предыдущий ответ:\n{json.dumps(data, ensure_ascii=False)}\n\n"
               f"Валидатор нашёл ошибки:\n" + "\n".join(errors) + "\nИсправь и верни полный JSON.")
        data = llm.extract_json(llm.chat(conn, fix, system=system, max_tokens=16000))
        errors = validate(data.get("definition") or {})
    definition = data.get("definition") or {}
    definition.setdefault("variables", [])
    definition.setdefault("outputs", [])
    for i, e in enumerate(definition.get("edges") or []):
        e.setdefault("id", f"e{i}")
        e.setdefault("sourceHandle", "out")
    auto_layout(definition)
    return {"name": data.get("name") or "Новый процесс", "description": data.get("description") or prompt[:300],
            "minutes_saved": int(data.get("minutes_saved") or 0), "definition": definition, "errors": errors}


@router.post("/api/vibe")
def vibe(body: VibeIn, db: Session = Depends(get_session), who: Principal = Depends(DEVELOP)) -> Dict[str, Any]:
    try:
        res = generate_process(_conn(db, body.connection), body.prompt)
    except llm.LLMError as exc:
        raise HTTPException(502, str(exc)) from None
    if body.create:
        p = Process(name=_free_name(db, res["name"]), description=res["description"], definition=res["definition"],
                    minutes_saved=res["minutes_saved"])
        db.add(p)
        db.commit()
        db.add(ProcessVersion(process_id=p.id, version=1, definition=p.definition, author=who.username))
        audit(db, who, "process.vibe", "process", p.id, {"prompt": body.prompt[:500]})
        res["id"] = p.id
        res["name"] = p.name
    return res


# ================================================================ аналитика
@router.get("/api/analytics", dependencies=[Depends(VIEW)])
def analytics(days: int = Query(30, ge=1, le=366), db: Session = Depends(get_session)) -> Dict[str, Any]:
    since = utcnow() - dt.timedelta(days=days)
    jobs = db.execute(select(Job).where(Job.created_at >= since)).scalars().all()
    minutes = {p.id: p.minutes_saved or 0 for p in db.execute(select(Process)).scalars()}
    hour_cost = float(os.environ.get("RPA_HOUR_COST", "0") or 0)
    daily: Dict[str, Dict[str, int]] = {}
    for i in range(days):
        d = (utcnow() - dt.timedelta(days=days - 1 - i)).date().isoformat()
        daily[d] = {"success": 0, "failed": 0, "cancelled": 0}
    per_proc: Dict[str, Dict[str, Any]] = {}
    per_robot: Dict[str, Dict[str, Any]] = {}
    saved_min = 0.0
    for j in jobs:
        d = j.created_at.date().isoformat()
        if j.status in ("success", "failed", "cancelled") and d in daily:
            daily[d][j.status] += 1
        pp = per_proc.setdefault(j.process_name, {"name": j.process_name, "process_id": j.process_id, "runs": 0,
                                                  "success": 0, "failed": 0, "seconds": 0.0, "minutes_saved": 0})
        pp["runs"] += 1
        if j.status in ("success", "failed"):
            pp[j.status] += 1
        if j.started_at and j.finished_at:
            dur = (j.finished_at - j.started_at).total_seconds()
            pp["seconds"] += dur
            if j.executed_by:
                rr = per_robot.setdefault(j.executed_by, {"name": j.executed_by, "runs": 0, "busy_seconds": 0.0})
                rr["runs"] += 1
                rr["busy_seconds"] += dur
        if j.status == "success":
            m = minutes.get(j.process_id or -1, 0)
            pp["minutes_saved"] += m
            saved_min += m
    procs = []
    for pp in per_proc.values():
        done = pp["success"] + pp["failed"]
        procs.append({**pp, "success_rate": round(pp["success"] / done, 3) if done else None,
                      "avg_seconds": round(pp["seconds"] / done, 1) if done else None,
                      "hours_saved": round(pp["minutes_saved"] / 60, 1)})
    procs.sort(key=lambda x: (-x["hours_saved"], -x["runs"]))
    period = days * 86400
    robots = [{**r, "utilization": round(r["busy_seconds"] / period, 4)} for r in per_robot.values()]
    for r in db.execute(select(Robot)).scalars():
        if r.name not in per_robot:
            robots.append({"name": r.name, "runs": 0, "busy_seconds": 0, "utilization": 0})
    robots.sort(key=lambda r: -r["busy_seconds"])
    finished = sum(1 for j in jobs if j.status in ("success", "failed"))
    success = sum(1 for j in jobs if j.status == "success")
    return {
        "days": days,
        "totals": {"runs": len(jobs), "success": success, "failed": sum(1 for j in jobs if j.status == "failed"),
                   "success_rate": round(success / finished, 3) if finished else None,
                   "hours_saved": round(saved_min / 60, 1), "fte_saved": round(saved_min / 60 / (days / 30 * 168), 2),
                   "money_saved": round(saved_min / 60 * hour_cost) if hour_cost else None},
        "daily": [{"date": d, **v} for d, v in daily.items()],
        "processes": procs, "robots": robots,
        "queues": [queue_out(q, db) for q in db.execute(select(WorkQueue).order_by(WorkQueue.name)).scalars()],
    }


# ================================================================ версии, импорт и экспорт
@router.get("/api/processes/{pid}/versions", dependencies=[Depends(VIEW)])
def versions(pid: int, db: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    rows = db.execute(select(ProcessVersion).where(ProcessVersion.process_id == pid)
                      .order_by(ProcessVersion.version.desc())).scalars()
    return [{"version": v.version, "author": v.author, "created_at": iso(v.created_at),
             "blocks": len((v.definition or {}).get("nodes", []))} for v in rows]


@router.post("/api/processes/{pid}/versions/{version}/restore")
def restore_version(pid: int, version: int, db: Session = Depends(get_session),
                    who: Principal = Depends(DEVELOP)) -> Dict[str, Any]:
    p = get_or_404(db, Process, pid)
    v = db.execute(select(ProcessVersion).where(ProcessVersion.process_id == pid,
                                                ProcessVersion.version == version)).scalar_one_or_none()
    if v is None:
        raise HTTPException(404, "Версия не найдена")
    p.definition, p.version, p.updated_at = v.definition, p.version + 1, utcnow()
    db.add(ProcessVersion(process_id=pid, version=p.version, definition=v.definition,
                          author=f"{who.username} (откат к v{version})"))
    db.commit()
    audit(db, who, "process.restore", "process", pid, {"from_version": version, "new_version": p.version})
    return {"id": p.id, "version": p.version}


@router.get("/api/processes/{pid}/export", dependencies=[Depends(VIEW)])
def export_process(pid: int, db: Session = Depends(get_session)) -> Response:
    p = get_or_404(db, Process, pid)
    data = {"format": "rpa-vibe-studio/process@1", "name": p.name, "description": p.description,
            "minutes_saved": p.minutes_saved or 0, "version": p.version, "definition": p.definition}
    from urllib.parse import quote

    fname = re.sub(r"[^\w\-]+", "_", p.name)[:80] + ".json"
    return Response(json.dumps(data, ensure_ascii=False, indent=2), media_type="application/json",
                    headers={"Content-Disposition": f"attachment; filename=\"process.json\"; "
                                                    f"filename*=UTF-8''{quote(fname)}"})


class ImportIn(BaseModel):
    name: str
    description: str = ""
    minutes_saved: int = 0
    definition: Dict[str, Any]


@router.post("/api/processes/import", status_code=201)
def import_process(body: ImportIn, db: Session = Depends(get_session),
                   who: Principal = Depends(DEVELOP)) -> Dict[str, Any]:
    errors = validate(body.definition)
    p = Process(name=_free_name(db, body.name), description=body.description, definition=body.definition,
                minutes_saved=body.minutes_saved)
    db.add(p)
    db.commit()
    db.add(ProcessVersion(process_id=p.id, version=1, definition=p.definition, author=who.username))
    audit(db, who, "process.import", "process", p.id, {"name": p.name})
    return {"id": p.id, "name": p.name, "errors": errors}

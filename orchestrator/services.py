"""Очередь задач, назначение роботам, расписания, контроль зависших запусков."""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import threading
from typing import Any, Dict, List, Optional, Set

from croniter import croniter
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .models import (
    AIConnection, AuditLog, Job, JobLog, OcrTemplate, Process, QueueItem, Robot, Schedule, Secret, SessionLocal,
    WorkQueue, utcnow,
)

log = logging.getLogger("rpa.orchestrator")
VERSION = "0.2.0"

ROBOT_ONLINE_SECONDS = 30      # робот онлайн, если heartbeat был не позже
ROBOT_LOST_SECONDS = 120       # запуск падает, если его робот молчит дольше
FINAL_STATUSES = ("success", "failed", "cancelled")


def robot_online(robot: Robot) -> bool:
    return robot.last_seen is not None and (utcnow() - robot.last_seen).total_seconds() <= ROBOT_ONLINE_SECONDS


def create_job(
    db: Session,
    process: Process,
    inputs: Optional[Dict[str, Any]] = None,
    required_tags: Optional[List[str]] = None,
    robot_name: Optional[str] = None,
    source: str = "manual",
    schedule_id: Optional[int] = None,
    started_by: Optional[str] = None,
    queue_id: Optional[int] = None,
) -> Job:
    job = Job(
        process_id=process.id,
        process_name=process.name,
        process_version=process.version,
        definition=process.definition,
        inputs=inputs or {},
        required_tags=sorted(set(required_tags or [])),
        robot_name=robot_name or None,
        source=source,
        schedule_id=schedule_id,
        started_by=started_by,
        queue_id=queue_id,
        status="queued",
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def claim_job(db: Session, robot: Robot) -> Optional[Job]:
    """Отдаёт роботу самую старую подходящую задачу из очереди (атомарно)."""
    tags = set(robot.tags or [])
    candidates = db.execute(
        select(Job).where(Job.status == "queued").order_by(Job.created_at, Job.id).limit(50)
    ).scalars().all()
    for job in candidates:
        if job.robot_name and job.robot_name != robot.name:
            continue
        if not set(job.required_tags or []) <= tags:
            continue
        res = db.execute(
            update(Job)
            .where(Job.id == job.id, Job.status == "queued")
            .values(status="running", executed_by=robot.name, started_at=utcnow())
        )
        if res.rowcount == 1:
            robot.current_job_id = job.id
            db.commit()
            db.refresh(job)
            return job
        db.rollback()  # задачу перехватил другой робот
    return None


def finish_job(db: Session, job: Job, status: str, error: Optional[str] = None,
               outputs: Optional[Dict[str, Any]] = None, failed_node: Optional[str] = None) -> None:
    job.status = status
    job.error = error
    job.outputs = outputs or {}
    job.failed_node = failed_node
    job.finished_at = utcnow()
    robot = db.execute(select(Robot).where(Robot.name == job.executed_by)).scalar_one_or_none()
    if robot and robot.current_job_id == job.id:
        robot.current_job_id = None
    db.commit()
    release_items(db, job.id)
    if status == "failed":
        notify_failure(job)


def next_run(cron: str, after: Optional[dt.datetime] = None) -> dt.datetime:
    """Следующий запуск по cron в локальном времени сервера; результат в UTC без tzinfo."""
    base_local = (after.replace(tzinfo=dt.timezone.utc).astimezone() if after
                  else dt.datetime.now().astimezone())
    nxt = croniter(cron, base_local).get_next(dt.datetime)
    return nxt.astimezone(dt.timezone.utc).replace(tzinfo=None)


def validate_cron(cron: str) -> bool:
    return croniter.is_valid(cron)


# ---------------------------------------------------------------- данные для робота
_SECRET_CALL = re.compile(r"""secret\(\s*['"]([^'"]+)['"]\s*\)""")


def referenced_secrets(definition: Dict[str, Any]) -> Set[str]:
    """Имена секретов, на которые ссылается процесс: параметры вида «secret» и вызовы secret('…')."""
    from rpa_engine import get_block

    names: Set[str] = set(_SECRET_CALL.findall(json.dumps(definition, ensure_ascii=False)))
    for node in definition.get("nodes") or []:
        try:
            bdef = get_block(node.get("type", ""))
        except KeyError:
            continue
        params = node.get("params") or {}
        for prm in bdef.params:
            if prm.kind == "secret":
                val = params.get(prm.name, prm.default)
                if isinstance(val, str) and val.strip() and "{{" not in val:
                    names.add(val.strip())
    return names


def ai_connections(db: Session, with_keys: bool = True) -> Dict[str, Dict[str, Any]]:
    from .security import decrypt

    out = {}
    for c in db.execute(select(AIConnection).order_by(AIConnection.name)).scalars():
        conn = {"name": c.name, "provider": c.provider, "model": c.model, "base_url": c.base_url,
                "folder_id": c.folder_id, "verify": c.verify, "is_default": c.is_default}
        if with_keys and c.secret_name:
            sec = db.execute(select(Secret).where(Secret.name == c.secret_name)).scalar_one_or_none()
            conn["api_key"] = decrypt(sec.value_enc) if sec else ""
        out[c.name] = conn
    return out


def job_payload(db: Session, job: Job) -> Dict[str, Any]:
    from .security import decrypt

    names = referenced_secrets(job.definition or {})
    secrets: Dict[str, str] = {}
    for sec in db.execute(select(Secret).where(Secret.name.in_(names))).scalars() if names else []:
        secrets[sec.name] = decrypt(sec.value_enc)
    if secrets:
        db.add(AuditLog(username=job.executed_by or "", action="secret.deliver", entity="job", entity_id=str(job.id),
                        details={"secrets": sorted(secrets)}))
        db.commit()
    templates = [t.definition for t in db.execute(select(OcrTemplate)).scalars()]
    return {"id": job.id, "process_name": job.process_name, "definition": job.definition, "inputs": job.inputs,
            "secrets": secrets, "config": {"ai": ai_connections(db), "ocr_templates": templates}}


# ---------------------------------------------------------------- очереди
def queue_by_name(db: Session, name: str) -> WorkQueue:
    q = db.execute(select(WorkQueue).where(WorkQueue.name == name)).scalar_one_or_none()
    if q is None:
        raise LookupError(f"Очередь «{name}» не найдена")
    return q


def add_items(db: Session, queue: WorkQueue, items: List[Dict[str, Any]]) -> int:
    added = 0
    now = utcnow()
    seen: Set[str] = set()
    for it in items:
        ref = it.get("reference")
        if ref:
            if str(ref) in seen:
                continue
            seen.add(str(ref))
            dup = db.execute(select(QueueItem.id).where(
                QueueItem.queue_id == queue.id, QueueItem.reference == str(ref),
                QueueItem.status.in_(("new", "in_progress", "done")))).first()
            if dup:
                continue
        db.add(QueueItem(queue_id=queue.id, reference=str(ref) if ref else None, data=it.get("data") or {},
                         priority=int(it.get("priority") or 0), created_at=now,
                         deadline=now + dt.timedelta(minutes=queue.sla_minutes) if queue.sla_minutes else None))
        added += 1
    db.commit()
    return added


def next_item(db: Session, queue: WorkQueue, robot: str, job_id: Optional[int]) -> Optional[QueueItem]:
    candidates = db.execute(
        select(QueueItem).where(QueueItem.queue_id == queue.id, QueueItem.status == "new")
        .order_by(QueueItem.priority.desc(), QueueItem.created_at, QueueItem.id).limit(20)
    ).scalars().all()
    for item in candidates:
        res = db.execute(update(QueueItem).where(QueueItem.id == item.id, QueueItem.status == "new").values(
            status="in_progress", locked_by=robot, job_id=job_id, started_at=utcnow()))
        if res.rowcount == 1:
            db.commit()
            db.refresh(item)
            return item
        db.rollback()
    return None


def finish_item(db: Session, item: QueueItem, status: str, result: Any = None, error: Optional[str] = None,
                business: bool = False) -> QueueItem:
    queue = db.get(WorkQueue, item.queue_id)
    item.finished_at = utcnow()
    item.result = result
    item.error = error
    item.business_error = business
    if status == "failed" and not business and queue and item.retries < (queue.max_retries or 0):
        item.status, item.retries, item.locked_by, item.job_id = "new", item.retries + 1, None, None
    else:
        item.status = status
    db.commit()
    return item


def release_items(db: Session, job_id: int) -> None:
    """Элементы, которые запуск взял и не закрыл (процесс упал), возвращаются в очередь как сбой."""
    for item in db.execute(select(QueueItem).where(QueueItem.job_id == job_id,
                                                   QueueItem.status == "in_progress")).scalars().all():
        finish_item(db, item, "failed", error="Запуск завершился, не закрыв элемент")


def run_queue_triggers(db: Session) -> int:
    created = 0
    for q in db.execute(select(WorkQueue).where(WorkQueue.process_id.isnot(None))).scalars().all():
        pending = db.scalar(select(func.count()).select_from(QueueItem).where(
            QueueItem.queue_id == q.id, QueueItem.status == "new")) or 0
        if not pending:
            continue
        active = db.scalar(select(func.count()).select_from(Job).where(
            Job.queue_id == q.id, Job.status.in_(("queued", "running")))) or 0
        process = db.get(Process, q.process_id)
        if process is None:
            continue
        for _ in range(max(0, min(pending, (q.max_parallel or 1) - active))):
            create_job(db, process, {"queue": q.name}, q.robot_tags or [], None, "queue", queue_id=q.id)
            created += 1
    return created


# ---------------------------------------------------------------- уведомления
def notify_failure(job: Job) -> None:
    """Сообщение о сбое: RPA_NOTIFY_TELEGRAM_TOKEN + RPA_NOTIFY_TELEGRAM_CHAT, RPA_NOTIFY_WEBHOOK."""
    tg_token, tg_chat = os.environ.get("RPA_NOTIFY_TELEGRAM_TOKEN"), os.environ.get("RPA_NOTIFY_TELEGRAM_CHAT")
    webhook = os.environ.get("RPA_NOTIFY_WEBHOOK")
    if not ((tg_token and tg_chat) or webhook):
        return
    url = os.environ.get("RPA_PUBLIC_URL", "").rstrip("/")
    text = (f"❌ Сбой процесса «{job.process_name}» (запуск #{job.id}, робот {job.executed_by or '—'})\n"
            f"{(job.error or '')[:600]}" + (f"\n{url}/#/jobs/{job.id}" if url else ""))

    def send() -> None:
        try:
            if tg_token and tg_chat:
                from rpa_engine.blocks.notify import telegram_send

                telegram_send(tg_token, tg_chat, text)
            if webhook:
                import httpx

                httpx.post(webhook, json={"text": text, "job_id": job.id, "process": job.process_name}, timeout=20)
        except Exception as exc:
            log.warning("Уведомление о сбое не отправлено: %s", exc)

    threading.Thread(target=send, daemon=True).start()


def run_due_schedules(db: Session) -> int:
    now = utcnow()
    due = db.execute(
        select(Schedule).where(Schedule.enabled.is_(True), Schedule.next_run_at <= now)
    ).scalars().all()
    created = 0
    for sch in due:
        process = db.get(Process, sch.process_id)
        if process is None:
            continue
        create_job(db, process, sch.inputs, sch.required_tags, sch.robot_name, "schedule", sch.id)
        sch.last_run_at = now
        sch.next_run_at = next_run(sch.cron, now)  # пропущенные запуски не догоняем
        db.commit()
        created += 1
    return created


def fail_lost_jobs(db: Session) -> int:
    """Запуски, чей робот перестал выходить на связь, помечаются упавшими."""
    limit = utcnow() - dt.timedelta(seconds=ROBOT_LOST_SECONDS)
    running = db.execute(select(Job).where(Job.status == "running")).scalars().all()
    count = 0
    for job in running:
        robot = db.execute(select(Robot).where(Robot.name == job.executed_by)).scalar_one_or_none()
        if robot is None or robot.last_seen < limit:
            reason = "Робот перестал отвечать"
        elif robot.current_job_id != job.id and job.started_at and job.started_at < limit:
            reason = "Робот на связи, но больше не выполняет этот запуск"
        else:
            continue
        db.add(JobLog(job_id=job.id, level="error", message=reason + ", запуск прерван"))
        finish_job(db, job, "failed", reason)
        count += 1
    return count


class Scheduler(threading.Thread):
    def __init__(self, interval: float = 10.0) -> None:
        super().__init__(daemon=True, name="rpa-scheduler")
        self.interval = interval
        self.stop_event = threading.Event()

    def run(self) -> None:
        while not self.stop_event.wait(self.interval):
            self.tick()

    def tick(self) -> None:
        db = SessionLocal()
        try:
            run_due_schedules(db)
            fail_lost_jobs(db)
            run_queue_triggers(db)
        except Exception:
            log.exception("Ошибка планировщика")
            db.rollback()
        finally:
            db.close()

    def stop(self) -> None:
        self.stop_event.set()

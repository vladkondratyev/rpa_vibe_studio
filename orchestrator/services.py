"""Очередь задач, назначение роботам, расписания, контроль зависших запусков."""
from __future__ import annotations

import datetime as dt
import logging
import threading
from typing import Any, Dict, List, Optional

from croniter import croniter
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .models import Job, JobLog, Process, Robot, Schedule, SessionLocal, utcnow

log = logging.getLogger("rpa.orchestrator")

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


def next_run(cron: str, after: Optional[dt.datetime] = None) -> dt.datetime:
    """Следующий запуск по cron в локальном времени сервера; результат в UTC без tzinfo."""
    base_local = (after.replace(tzinfo=dt.timezone.utc).astimezone() if after
                  else dt.datetime.now().astimezone())
    nxt = croniter(cron, base_local).get_next(dt.datetime)
    return nxt.astimezone(dt.timezone.utc).replace(tzinfo=None)


def validate_cron(cron: str) -> bool:
    return croniter.is_valid(cron)


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
        except Exception:
            log.exception("Ошибка планировщика")
            db.rollback()
        finally:
            db.close()

    def stop(self) -> None:
        self.stop_event.set()

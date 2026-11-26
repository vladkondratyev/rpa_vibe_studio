"""HTTP API оркестратора и раздача веб-интерфейса.

Запуск:  uvicorn orchestrator.main:app --host 0.0.0.0 --port 8000
Токен доступа (рекомендуется):  RPA_TOKEN=секрет  — его же указывают роботам и в веб-интерфейсе.
"""
from __future__ import annotations

import base64
import datetime as dt
import hmac
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from rpa_engine import catalog, validate

from . import services
from .models import (
    EMPTY_PROCESS, Artifact, Job, JobLog, Process, ProcessVersion, Robot, Schedule, get_session, init_db, utcnow,
)
from .security import ADMIN, DEVELOP, OPERATE, ROBOT, VIEW, Principal, audit

log = logging.getLogger("rpa.orchestrator")
TOKEN = os.environ.get("RPA_TOKEN", "")
FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if not TOKEN:
        log.warning("RPA_TOKEN не задан: пока нет пользователей, API открыт (допустимо только для разработки)")
    scheduler = services.Scheduler()
    scheduler.start()
    yield
    scheduler.stop()


app = FastAPI(title="RPA Vibe Studio", version=services.VERSION, lifespan=lifespan)




def iso(value: Optional[dt.datetime]) -> Optional[str]:
    return value.isoformat() + "Z" if value else None


# ---------------------------------------------------------------- схемы
class ProcessIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    definition: Optional[Dict[str, Any]] = None
    minutes_saved: Optional[int] = None


class RunIn(BaseModel):
    inputs: Dict[str, Any] = {}
    tags: List[str] = []
    robot: Optional[str] = None


class ScheduleIn(BaseModel):
    process_id: int
    cron: str
    enabled: bool = True
    inputs: Dict[str, Any] = {}
    tags: List[str] = []
    robot: Optional[str] = None


class HeartbeatIn(BaseModel):
    name: str
    hostname: str = ""
    os: str = ""
    tags: List[str] = []
    version: str = ""
    current_job: Optional[int] = None


class ClaimIn(BaseModel):
    name: str


class LogEntry(BaseModel):
    ts: Optional[float] = None
    level: str = "info"
    node: Optional[str] = None
    message: str


class LogsIn(BaseModel):
    entries: List[LogEntry]


class ArtifactIn(BaseModel):
    name: str
    content_type: str = "image/png"
    node: Optional[str] = None
    data_b64: str


class FinishIn(BaseModel):
    status: str
    error: Optional[str] = None
    outputs: Dict[str, Any] = {}
    failed_node: Optional[str] = None


# ---------------------------------------------------------------- сериализация
def process_out(p: Process, full: bool = True) -> Dict[str, Any]:
    out = {"id": p.id, "name": p.name, "description": p.description, "version": p.version,
           "minutes_saved": p.minutes_saved or 0,
           "created_at": iso(p.created_at), "updated_at": iso(p.updated_at)}
    if full:
        out["definition"] = p.definition
    return out


def job_out(j: Job) -> Dict[str, Any]:
    return {
        "id": j.id, "process_id": j.process_id, "process_name": j.process_name,
        "process_version": j.process_version, "status": j.status, "error": j.error,
        "failed_node": j.failed_node, "inputs": j.inputs, "outputs": j.outputs,
        "required_tags": j.required_tags, "robot_name": j.robot_name, "executed_by": j.executed_by,
        "source": j.source, "cancel_requested": j.cancel_requested, "queue_id": j.queue_id,
        "started_by": j.started_by,
        "created_at": iso(j.created_at), "started_at": iso(j.started_at), "finished_at": iso(j.finished_at),
    }


def robot_out(r: Robot) -> Dict[str, Any]:
    return {"id": r.id, "name": r.name, "hostname": r.hostname, "os": r.os, "tags": r.tags,
            "version": r.agent_version, "current_job_id": r.current_job_id,
            "online": services.robot_online(r), "last_seen": iso(r.last_seen)}


def schedule_out(s: Schedule, db: Session) -> Dict[str, Any]:
    p = db.get(Process, s.process_id)
    return {"id": s.id, "process_id": s.process_id, "process_name": p.name if p else None, "cron": s.cron,
            "enabled": s.enabled, "inputs": s.inputs, "tags": s.required_tags, "robot": s.robot_name,
            "next_run_at": iso(s.next_run_at), "last_run_at": iso(s.last_run_at)}


def get_or_404(db: Session, model: Any, id: int) -> Any:
    obj = db.get(model, id)
    if obj is None:
        raise HTTPException(404, "Не найдено")
    return obj


# ---------------------------------------------------------------- общее
@app.get("/api/health")
def health(db: Session = Depends(get_session)) -> Dict[str, Any]:
    from .security import auth_enabled

    return {"ok": True, "auth": auth_enabled(db), "version": services.VERSION}


@app.get("/api/blocks", dependencies=[Depends(VIEW)])
def blocks() -> List[Dict[str, Any]]:
    return catalog()


@app.get("/api/stats", dependencies=[Depends(VIEW)])
def stats(db: Session = Depends(get_session)) -> Dict[str, Any]:
    rows = db.execute(select(Job.status, func.count()).group_by(Job.status)).all()
    robots = db.execute(select(Robot)).scalars().all()
    return {"jobs": {s: c for s, c in rows},
            "robots_online": sum(1 for r in robots if services.robot_online(r)),
            "robots_total": len(robots),
            "processes": db.scalar(select(func.count()).select_from(Process))}


# ---------------------------------------------------------------- процессы
@app.get("/api/processes", dependencies=[Depends(VIEW)])
def list_processes(db: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    return [process_out(p, full=False) for p in db.execute(select(Process).order_by(Process.name)).scalars()]


@app.post("/api/processes", dependencies=[Depends(DEVELOP)], status_code=201)
def create_process(body: ProcessIn, db: Session = Depends(get_session),
                   who: Principal = Depends(DEVELOP)) -> Dict[str, Any]:
    if db.execute(select(Process).where(Process.name == body.name)).scalar_one_or_none():
        raise HTTPException(409, "Процесс с таким именем уже есть")
    p = Process(name=body.name, description=body.description, definition=body.definition or EMPTY_PROCESS,
                minutes_saved=body.minutes_saved or 0)
    db.add(p)
    db.commit()
    db.add(ProcessVersion(process_id=p.id, version=1, definition=p.definition, author=who.username))
    audit(db, who, "process.create", "process", p.id, {"name": p.name})
    return process_out(p)


@app.get("/api/processes/{pid}", dependencies=[Depends(VIEW)])
def get_process(pid: int, db: Session = Depends(get_session)) -> Dict[str, Any]:
    return process_out(get_or_404(db, Process, pid))


@app.put("/api/processes/{pid}", dependencies=[Depends(DEVELOP)])
def update_process(pid: int, body: ProcessIn, db: Session = Depends(get_session),
                   who: Principal = Depends(DEVELOP)) -> Dict[str, Any]:
    p = get_or_404(db, Process, pid)
    clash = db.execute(select(Process).where(Process.name == body.name, Process.id != pid)).scalar_one_or_none()
    if clash:
        raise HTTPException(409, "Процесс с таким именем уже есть")
    p.name, p.description = body.name, body.description
    if body.minutes_saved is not None:
        p.minutes_saved = body.minutes_saved
    changed = body.definition is not None and body.definition != p.definition
    if changed:
        p.definition = body.definition
        p.version += 1
        db.add(ProcessVersion(process_id=p.id, version=p.version, definition=p.definition, author=who.username))
    p.updated_at = utcnow()
    db.commit()
    if changed:
        audit(db, who, "process.update", "process", p.id, {"version": p.version})
    return process_out(p)


@app.delete("/api/processes/{pid}", dependencies=[Depends(DEVELOP)], status_code=204)
def delete_process(pid: int, db: Session = Depends(get_session), who: Principal = Depends(DEVELOP)) -> Response:
    p = get_or_404(db, Process, pid)
    db.execute(delete(Schedule).where(Schedule.process_id == pid))
    db.delete(p)
    db.commit()
    audit(db, who, "process.delete", "process", pid, {"name": p.name})
    return Response(status_code=204)


@app.post("/api/processes/{pid}/validate", dependencies=[Depends(VIEW)])
def validate_process(pid: int, db: Session = Depends(get_session)) -> Dict[str, Any]:
    errors = validate(get_or_404(db, Process, pid).definition)
    return {"ok": not errors, "errors": errors}


@app.post("/api/processes/{pid}/run", dependencies=[Depends(OPERATE)], status_code=201)
def run_process(pid: int, body: RunIn, db: Session = Depends(get_session),
                who: Principal = Depends(OPERATE)) -> Dict[str, Any]:
    p = get_or_404(db, Process, pid)
    errors = validate(p.definition)
    if errors:
        raise HTTPException(422, {"message": "Процесс содержит ошибки", "errors": errors})
    job = services.create_job(db, p, body.inputs, body.tags, body.robot, "manual", started_by=who.username)
    audit(db, who, "process.run", "job", job.id, {"process": p.name})
    return job_out(job)


# ---------------------------------------------------------------- запуски
@app.get("/api/jobs", dependencies=[Depends(VIEW)])
def list_jobs(
    process_id: Optional[int] = None,
    status: Optional[str] = None,
    limit: int = Query(100, le=500),
    db: Session = Depends(get_session),
) -> List[Dict[str, Any]]:
    q = select(Job).order_by(Job.id.desc()).limit(limit)
    if process_id:
        q = q.where(Job.process_id == process_id)
    if status:
        q = q.where(Job.status == status)
    return [job_out(j) for j in db.execute(q).scalars()]


@app.get("/api/jobs/{jid}", dependencies=[Depends(VIEW)])
def get_job(jid: int, db: Session = Depends(get_session)) -> Dict[str, Any]:
    return job_out(get_or_404(db, Job, jid))


@app.get("/api/jobs/{jid}/logs", dependencies=[Depends(VIEW)])
def job_logs(jid: int, after: int = 0, db: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    rows = db.execute(
        select(JobLog).where(JobLog.job_id == jid, JobLog.id > after).order_by(JobLog.id).limit(2000)
    ).scalars()
    return [{"id": r.id, "ts": iso(r.ts), "level": r.level, "node_id": r.node_id, "message": r.message}
            for r in rows]


@app.get("/api/jobs/{jid}/artifacts", dependencies=[Depends(VIEW)])
def job_artifacts(jid: int, db: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    rows = db.execute(
        select(Artifact.id, Artifact.name, Artifact.content_type, Artifact.node_id, Artifact.created_at)
        .where(Artifact.job_id == jid).order_by(Artifact.id)
    ).all()
    return [{"id": r.id, "name": r.name, "content_type": r.content_type, "node_id": r.node_id,
             "created_at": iso(r.created_at)} for r in rows]


@app.get("/api/artifacts/{aid}", dependencies=[Depends(VIEW)])
def get_artifact(aid: int, db: Session = Depends(get_session)) -> Response:
    a = get_or_404(db, Artifact, aid)
    from urllib.parse import quote

    # имя может быть кириллическим: заголовки HTTP — latin-1, поэтому filename* по RFC 5987
    ascii_name = a.name.encode("ascii", "replace").decode().replace('"', "")
    return Response(a.data, media_type=a.content_type, headers={
        "Content-Disposition": f"inline; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(a.name)}"})


@app.post("/api/jobs/{jid}/cancel", dependencies=[Depends(OPERATE)])
def cancel_job(jid: int, db: Session = Depends(get_session), who: Principal = Depends(OPERATE)) -> Dict[str, Any]:
    job = get_or_404(db, Job, jid)
    audit(db, who, "job.cancel", "job", jid, commit=False)
    if job.status == "queued":
        job.status, job.finished_at, job.error = "cancelled", utcnow(), "Отменён до запуска"
    elif job.status == "running":
        job.cancel_requested = True
    db.commit()
    return job_out(job)


# ---------------------------------------------------------------- роботы (для интерфейса)
@app.get("/api/robots", dependencies=[Depends(VIEW)])
def list_robots(db: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    return [robot_out(r) for r in db.execute(select(Robot).order_by(Robot.name)).scalars()]


@app.delete("/api/robots/{rid}", dependencies=[Depends(ADMIN)], status_code=204)
def delete_robot(rid: int, db: Session = Depends(get_session)) -> Response:
    db.delete(get_or_404(db, Robot, rid))
    db.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------- расписания
def _apply_schedule(s: Schedule, body: ScheduleIn, db: Session) -> None:
    if not services.validate_cron(body.cron):
        raise HTTPException(422, "Неверное cron-выражение")
    get_or_404(db, Process, body.process_id)
    s.process_id, s.cron, s.enabled = body.process_id, body.cron, body.enabled
    s.inputs, s.required_tags, s.robot_name = body.inputs, sorted(set(body.tags)), body.robot or None
    s.next_run_at = services.next_run(body.cron) if body.enabled else None


@app.get("/api/schedules", dependencies=[Depends(VIEW)])
def list_schedules(db: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    return [schedule_out(s, db) for s in db.execute(select(Schedule).order_by(Schedule.id)).scalars()]


@app.post("/api/schedules", dependencies=[Depends(DEVELOP)], status_code=201)
def create_schedule(body: ScheduleIn, db: Session = Depends(get_session)) -> Dict[str, Any]:
    s = Schedule()
    _apply_schedule(s, body, db)
    db.add(s)
    db.commit()
    return schedule_out(s, db)


@app.put("/api/schedules/{sid}", dependencies=[Depends(DEVELOP)])
def update_schedule(sid: int, body: ScheduleIn, db: Session = Depends(get_session)) -> Dict[str, Any]:
    s = get_or_404(db, Schedule, sid)
    _apply_schedule(s, body, db)
    db.commit()
    return schedule_out(s, db)


@app.delete("/api/schedules/{sid}", dependencies=[Depends(DEVELOP)], status_code=204)
def delete_schedule(sid: int, db: Session = Depends(get_session)) -> Response:
    db.delete(get_or_404(db, Schedule, sid))
    db.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------- API для роботов
@app.post("/api/agent/heartbeat", dependencies=[Depends(ROBOT)])
def heartbeat(body: HeartbeatIn, db: Session = Depends(get_session)) -> Dict[str, Any]:
    robot = db.execute(select(Robot).where(Robot.name == body.name)).scalar_one_or_none()
    if robot is None:
        robot = Robot(name=body.name)
        db.add(robot)
    robot.hostname, robot.os, robot.tags = body.hostname, body.os, sorted(set(body.tags))
    robot.agent_version, robot.last_seen = body.version, utcnow()
    robot.current_job_id = body.current_job
    db.commit()
    cancel = False
    if body.current_job:
        job = db.get(Job, body.current_job)
        cancel = bool(job and job.cancel_requested)
    return {"ok": True, "cancel": cancel}


@app.post("/api/agent/claim", dependencies=[Depends(ROBOT)])
def claim(body: ClaimIn, db: Session = Depends(get_session)) -> Response:
    robot = db.execute(select(Robot).where(Robot.name == body.name)).scalar_one_or_none()
    if robot is None:
        raise HTTPException(409, "Робот не зарегистрирован: сначала heartbeat")
    robot.last_seen = utcnow()
    db.commit()
    job = services.claim_job(db, robot)
    if job is None:
        return Response(status_code=204)
    return _json(services.job_payload(db, job))


def _agent_job(db: Session, jid: int) -> Job:
    job = get_or_404(db, Job, jid)
    return job


@app.post("/api/agent/jobs/{jid}/logs", dependencies=[Depends(ROBOT)])
def agent_logs(jid: int, body: LogsIn, db: Session = Depends(get_session)) -> Dict[str, Any]:
    job = _agent_job(db, jid)
    for e in body.entries:
        ts = dt.datetime.fromtimestamp(e.ts, dt.timezone.utc).replace(tzinfo=None) if e.ts else utcnow()
        db.add(JobLog(job_id=jid, ts=ts, level=e.level, node_id=e.node, message=e.message[:20000]))
    db.commit()
    return {"cancel": bool(job.cancel_requested)}


@app.post("/api/agent/jobs/{jid}/artifacts", dependencies=[Depends(ROBOT)], status_code=201)
def agent_artifact(jid: int, body: ArtifactIn, db: Session = Depends(get_session)) -> Dict[str, Any]:
    _agent_job(db, jid)
    a = Artifact(job_id=jid, name=body.name, content_type=body.content_type, node_id=body.node,
                 data=base64.b64decode(body.data_b64))
    db.add(a)
    db.commit()
    return {"id": a.id}


@app.post("/api/agent/jobs/{jid}/finish", dependencies=[Depends(ROBOT)])
def agent_finish(jid: int, body: FinishIn, db: Session = Depends(get_session)) -> Dict[str, Any]:  # noqa: D401
    job = _agent_job(db, jid)
    if body.status not in services.FINAL_STATUSES:
        raise HTTPException(422, "Недопустимый статус")
    if job.status in services.FINAL_STATUSES:
        return {"ok": False, "reason": f"Запуск уже завершён со статусом {job.status}"}
    services.finish_job(db, job, body.status, body.error, body.outputs, body.failed_node)
    return {"ok": True}


def _json(data: Dict[str, Any]) -> Response:
    import json

    return Response(json.dumps(data, ensure_ascii=False, default=str), media_type="application/json")


# ---------------------------------------------------------------- расширения 0.2
from .api_extra import router as extra_router  # noqa: E402

app.include_router(extra_router)

# ---------------------------------------------------------------- веб-интерфейс
if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        if path.startswith("api/"):
            raise HTTPException(404)
        target = FRONTEND_DIST / path
        if path and target.is_file() and FRONTEND_DIST in target.resolve().parents:
            return FileResponse(target)
        return FileResponse(FRONTEND_DIST / "index.html")

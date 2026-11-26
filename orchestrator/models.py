"""Модель данных оркестратора. По умолчанию SQLite, в продакшене PostgreSQL
(переменная окружения RPA_DATABASE_URL, например postgresql+psycopg://user:pass@host/rpa)."""
from __future__ import annotations

import datetime as dt
import os
from typing import Iterator

from sqlalchemy import (
    JSON, Boolean, Column, DateTime, ForeignKey, Integer, LargeBinary, String, Text, create_engine,
)
from sqlalchemy.orm import Session, declarative_base, sessionmaker

DATABASE_URL = os.environ.get("RPA_DATABASE_URL", "sqlite:///./rpa.db")

_engine_kwargs = {"future": True}
if DATABASE_URL.startswith("sqlite"):
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
engine = create_engine(DATABASE_URL, **_engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
Base = declarative_base()


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


EMPTY_PROCESS = {
    "variables": [],
    "outputs": [],
    "nodes": [{"id": "start", "type": "core.start", "params": {}, "position": {"x": 80, "y": 160}},
              {"id": "end", "type": "core.end", "params": {}, "position": {"x": 420, "y": 160}}],
    "edges": [{"id": "e-start-end", "source": "start", "sourceHandle": "out", "target": "end"}],
}


class Process(Base):
    __tablename__ = "processes"
    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False, unique=True)
    description = Column(Text, default="")
    definition = Column(JSON, nullable=False)
    version = Column(Integer, nullable=False, default=1)
    minutes_saved = Column(Integer, default=0)  # ручной труд за один успешный запуск, минут
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow)


class ProcessVersion(Base):
    __tablename__ = "process_versions"
    id = Column(Integer, primary_key=True)
    process_id = Column(Integer, index=True, nullable=False)
    version = Column(Integer, nullable=False)
    definition = Column(JSON, nullable=False)
    author = Column(String(200), default="")
    created_at = Column(DateTime, default=utcnow)


class Robot(Base):
    __tablename__ = "robots"
    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False, unique=True)
    hostname = Column(String(200), default="")
    os = Column(String(50), default="")
    tags = Column(JSON, default=list)
    agent_version = Column(String(50), default="")
    current_job_id = Column(Integer, nullable=True)
    last_seen = Column(DateTime, default=utcnow)
    registered_at = Column(DateTime, default=utcnow)


class Job(Base):
    __tablename__ = "jobs"
    id = Column(Integer, primary_key=True)
    process_id = Column(Integer, nullable=True, index=True)  # процесс могут удалить — запуск остаётся
    process_name = Column(String(200), nullable=False)
    process_version = Column(Integer, nullable=False)
    definition = Column(JSON, nullable=False)  # снимок процесса на момент запуска
    inputs = Column(JSON, default=dict)
    outputs = Column(JSON, default=dict)
    required_tags = Column(JSON, default=list)
    robot_name = Column(String(200), nullable=True)  # если задан — только этот робот
    status = Column(String(20), nullable=False, default="queued", index=True)
    error = Column(Text, nullable=True)
    failed_node = Column(String(100), nullable=True)
    cancel_requested = Column(Boolean, default=False)
    source = Column(String(50), default="manual")  # manual | schedule | api
    schedule_id = Column(Integer, nullable=True)
    queue_id = Column(Integer, nullable=True, index=True)  # запуск по триггеру очереди
    started_by = Column(String(200), nullable=True)
    created_at = Column(DateTime, default=utcnow, index=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    executed_by = Column(String(200), nullable=True)


class JobLog(Base):
    __tablename__ = "job_logs"
    id = Column(Integer, primary_key=True)
    job_id = Column(Integer, ForeignKey("jobs.id", ondelete="CASCADE"), index=True, nullable=False)
    ts = Column(DateTime, default=utcnow)
    level = Column(String(20), default="info")
    node_id = Column(String(100), nullable=True)
    message = Column(Text, nullable=False)


class Artifact(Base):
    __tablename__ = "artifacts"
    id = Column(Integer, primary_key=True)
    job_id = Column(Integer, ForeignKey("jobs.id", ondelete="CASCADE"), index=True, nullable=False)
    name = Column(String(200), nullable=False)
    content_type = Column(String(100), default="application/octet-stream")
    node_id = Column(String(100), nullable=True)
    data = Column(LargeBinary, nullable=False)
    created_at = Column(DateTime, default=utcnow)


class Schedule(Base):
    __tablename__ = "schedules"
    id = Column(Integer, primary_key=True)
    process_id = Column(Integer, ForeignKey("processes.id", ondelete="CASCADE"), nullable=False)
    cron = Column(String(100), nullable=False)
    enabled = Column(Boolean, default=True)
    inputs = Column(JSON, default=dict)
    required_tags = Column(JSON, default=list)
    robot_name = Column(String(200), nullable=True)
    next_run_at = Column(DateTime, nullable=True)
    last_run_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow)


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String(200), nullable=False, unique=True)
    full_name = Column(String(200), default="")
    password_hash = Column(String(300), default="")
    role = Column(String(20), nullable=False, default="viewer")  # admin|developer|operator|viewer|robot
    source = Column(String(20), default="local")  # local | ldap
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=utcnow)
    last_login = Column(DateTime, nullable=True)


class ApiToken(Base):
    __tablename__ = "api_tokens"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    token_hash = Column(String(100), nullable=False, unique=True)
    name = Column(String(200), default="")
    kind = Column(String(20), default="session")  # session | api
    created_at = Column(DateTime, default=utcnow)
    expires_at = Column(DateTime, nullable=True)
    last_used = Column(DateTime, nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id = Column(Integer, primary_key=True)
    ts = Column(DateTime, default=utcnow, index=True)
    username = Column(String(200), default="")
    action = Column(String(100), nullable=False)
    entity = Column(String(50), default="")
    entity_id = Column(String(100), default="")
    details = Column(JSON, default=dict)


class Secret(Base):
    __tablename__ = "secrets"
    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False, unique=True)
    value_enc = Column(Text, nullable=False)
    description = Column(Text, default="")
    updated_by = Column(String(200), default="")
    updated_at = Column(DateTime, default=utcnow)


class WorkQueue(Base):
    __tablename__ = "work_queues"
    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False, unique=True)
    description = Column(Text, default="")
    max_retries = Column(Integer, default=2)
    sla_minutes = Column(Integer, default=0)  # 0 — без SLA
    process_id = Column(Integer, nullable=True)  # триггер: запускать процесс при новых элементах
    max_parallel = Column(Integer, default=1)
    robot_tags = Column(JSON, default=list)
    created_at = Column(DateTime, default=utcnow)


class QueueItem(Base):
    __tablename__ = "queue_items"
    id = Column(Integer, primary_key=True)
    queue_id = Column(Integer, ForeignKey("work_queues.id", ondelete="CASCADE"), index=True, nullable=False)
    reference = Column(String(300), nullable=True, index=True)
    data = Column(JSON, default=dict)
    priority = Column(Integer, default=0)
    status = Column(String(20), default="new", index=True)  # new | in_progress | done | failed
    retries = Column(Integer, default=0)
    result = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)
    business_error = Column(Boolean, default=False)
    locked_by = Column(String(200), nullable=True)
    job_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=utcnow, index=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    deadline = Column(DateTime, nullable=True)


class AIConnection(Base):
    __tablename__ = "ai_connections"
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False, unique=True)
    provider = Column(String(30), nullable=False)
    model = Column(String(200), default="")
    base_url = Column(String(500), default="")
    folder_id = Column(String(200), default="")  # Yandex Cloud: каталог; GigaChat: scope
    secret_name = Column(String(200), default="")  # ключ API — в хранилище секретов
    verify = Column(Boolean, default=True)
    is_default = Column(Boolean, default=False)
    description = Column(Text, default="")


class OcrTemplate(Base):
    __tablename__ = "ocr_templates"
    id = Column(Integer, primary_key=True)
    slug = Column(String(100), nullable=False, unique=True)
    definition = Column(JSON, nullable=False)
    updated_by = Column(String(200), default="")
    updated_at = Column(DateTime, default=utcnow)


# Колонки, добавленные после 0.1.0: create_all не меняет существующие таблицы.
_MIGRATIONS = {
    "processes": {"minutes_saved": "INTEGER DEFAULT 0"},
    "jobs": {"queue_id": "INTEGER", "started_by": "VARCHAR(200)"},
}


def init_db() -> None:
    from sqlalchemy import inspect, text

    Base.metadata.create_all(engine)
    insp = inspect(engine)
    with engine.begin() as conn:
        for table, cols in _MIGRATIONS.items():
            existing = {c["name"] for c in insp.get_columns(table)}
            for col, ddl in cols.items():
                if col not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))


def get_session() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()

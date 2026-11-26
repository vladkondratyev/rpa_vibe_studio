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
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow)


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


def init_db() -> None:
    Base.metadata.create_all(engine)


def get_session() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()

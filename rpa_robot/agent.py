"""Агент робота: регистрируется в оркестраторе, забирает задачи из очереди,
выполняет процессы движком rpa_engine и отправляет журнал, скриншоты и итог."""
from __future__ import annotations

import base64
import logging
import platform
import queue
import socket
import threading
import time
from typing import Any, Dict, List, Optional

import httpx

from rpa_engine import Context, run_process

VERSION = "0.2.0"
log = logging.getLogger("rpa.robot")


class Agent:
    def __init__(self, server: str, name: str, tags: List[str], token: str = "",
                 poll_interval: float = 3.0, heartbeat_interval: float = 10.0) -> None:
        self.server = server.rstrip("/")
        self.name = name
        self.tags = sorted(set(tags) | {f"os={_os_tag()}"})
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.http = httpx.Client(base_url=self.server, headers=headers, timeout=30)
        self.poll_interval = poll_interval
        self.heartbeat_interval = heartbeat_interval
        self.current_job: Optional[int] = None
        self.current_ctx: Optional[Context] = None
        self.stop_event = threading.Event()

    # ------------------------------------------------------------ связь
    def heartbeat(self) -> None:
        r = self.http.post("/api/agent/heartbeat", json={
            "name": self.name, "hostname": socket.gethostname(), "os": _os_tag(),
            "tags": self.tags, "version": VERSION, "current_job": self.current_job,
        })
        r.raise_for_status()
        if r.json().get("cancel") and self.current_ctx:
            self.current_ctx.cancel_event.set()

    def _heartbeat_loop(self) -> None:
        while not self.stop_event.wait(self.heartbeat_interval):
            try:
                self.heartbeat()
            except Exception as exc:
                log.warning("heartbeat не прошёл: %s", exc)

    # ------------------------------------------------------------ основной цикл
    def run_forever(self) -> None:
        log.info("Робот %s подключается к %s, метки: %s", self.name, self.server, ", ".join(self.tags))
        while not self.stop_event.is_set():
            try:
                self.heartbeat()
                break
            except Exception as exc:
                log.error("Оркестратор недоступен (%s), повтор через 5 с", exc)
                time.sleep(5)
        threading.Thread(target=self._heartbeat_loop, daemon=True).start()
        log.info("Робот на связи, ожидаю задачи")
        while not self.stop_event.is_set():
            try:
                r = self.http.post("/api/agent/claim", json={"name": self.name})
                if r.status_code == 204:
                    self.stop_event.wait(self.poll_interval)
                    continue
                r.raise_for_status()
                self.execute(r.json())
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                log.error("Ошибка связи с оркестратором: %s", exc)
                self.stop_event.wait(5)

    def execute(self, job: Dict[str, Any]) -> None:
        job_id = job["id"]
        log.info("Запуск #%s: %s", job_id, job["process_name"])
        sender = _LogSender(self.http, job_id)
        ctx = Context(log=sender.log, artifact=sender.artifact, secrets=job.get("secrets") or {},
                      config=job.get("config") or {}, orchestrator=OrchestratorClient(self.http, self.name, job_id))
        self.current_job, self.current_ctx = job_id, ctx
        sender.on_cancel = ctx.cancel_event.set
        sender.start()
        try:
            result = run_process(job["definition"], job.get("inputs") or {}, ctx)
        except Exception as exc:  # движок не должен падать, но страхуемся
            log.exception("Сбой движка")
            from rpa_engine.runner import RunResult
            result = RunResult(status="failed", error=f"Сбой движка: {exc}")
        finally:
            sender.close()
        for attempt in range(10):
            try:
                self.http.post(f"/api/agent/jobs/{job_id}/finish", json={
                    "status": result.status, "error": result.error,
                    "outputs": result.outputs, "failed_node": result.failed_node,
                }).raise_for_status()
                break
            except Exception as exc:
                log.warning("Не удалось отправить итог (%s), попытка %s", exc, attempt + 1)
                time.sleep(min(30, 2 ** attempt))
        self.current_job, self.current_ctx = None, None
        log.info("Запуск #%s завершён: %s", job_id, result.status)


class OrchestratorClient:
    """То, что блоки процесса могут попросить у оркестратора: очереди и запуск процессов."""

    def __init__(self, http: httpx.Client, robot: str, job_id: int) -> None:
        self.http, self.robot, self.job_id = http, robot, job_id

    def _ok(self, r: Any) -> Any:
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail")
            except ValueError:
                detail = r.text
            raise RuntimeError(f"Оркестратор: {detail}")
        return r

    def queue_add(self, queue: str, items: List[Dict[str, Any]]) -> int:
        return self._ok(self.http.post(f"/api/agent/queues/{queue}/items", json={"items": items})).json()["added"]

    def queue_next(self, queue: str) -> Optional[Dict[str, Any]]:
        r = self._ok(self.http.post(f"/api/agent/queues/{queue}/next",
                                    json={"robot": self.robot, "job_id": self.job_id}))
        return None if r.status_code == 204 else r.json()

    def queue_finish(self, item_id: int, status: str, result: Any = None, error: Optional[str] = None,
                     business: bool = False) -> None:
        self._ok(self.http.post(f"/api/agent/queue-items/{item_id}/finish",
                                json={"status": status, "result": result, "error": error, "business": business}))

    def run_process(self, name: str, inputs: Dict[str, Any], wait: bool = False,
                    check_cancel: Any = None, timeout: float = 6 * 3600) -> Dict[str, Any]:
        job = self._ok(self.http.post("/api/agent/run", json={"name": name, "inputs": inputs,
                                                              "parent_job": self.job_id})).json()
        if not wait:
            return job
        deadline = time.time() + timeout
        while time.time() < deadline:
            if check_cancel:
                check_cancel()
            job = self._ok(self.http.get(f"/api/agent/jobs/{job['id']}")).json()
            if job["status"] in ("success", "failed", "cancelled"):
                return job
            time.sleep(3)
        raise RuntimeError(f"Процесс «{name}» не завершился за отведённое время")


class _LogSender(threading.Thread):
    """Буферизует журнал и раз в секунду отправляет пачкой, чтобы не тормозить процесс."""

    def __init__(self, http: httpx.Client, job_id: int) -> None:
        super().__init__(daemon=True)
        self.http, self.job_id = http, job_id
        self.q: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        self.done = threading.Event()
        self.on_cancel = lambda: None

    def log(self, level: str, message: str, node: Optional[str]) -> None:
        if level != "debug":
            log.info("[%s] %s", node or "-", message.splitlines()[0] if message else "")
        self.q.put({"ts": time.time(), "level": level, "node": node, "message": message})

    def artifact(self, name: str, data: bytes, content_type: str, node: Optional[str]) -> None:
        try:
            self.http.post(f"/api/agent/jobs/{self.job_id}/artifacts", json={
                "name": name, "content_type": content_type, "node": node,
                "data_b64": base64.b64encode(data).decode(),
            }).raise_for_status()
        except Exception as exc:
            log.warning("Артефакт %s не отправлен: %s", name, exc)

    def run(self) -> None:
        while not self.done.is_set():
            self.done.wait(1.0)
            self.flush()

    def flush(self) -> None:
        batch = []
        while not self.q.empty() and len(batch) < 500:
            batch.append(self.q.get_nowait())
        if not batch:
            return
        try:
            r = self.http.post(f"/api/agent/jobs/{self.job_id}/logs", json={"entries": batch})
            r.raise_for_status()
            if r.json().get("cancel"):
                self.on_cancel()
        except Exception as exc:
            log.warning("Журнал не отправлен (%s), повторю", exc)
            for item in batch:
                self.q.put(item)

    def close(self) -> None:
        self.done.set()
        self.join(timeout=5)
        for _ in range(5):
            if self.q.empty():
                break
            self.flush()


def _os_tag() -> str:
    return {"Darwin": "macos"}.get(platform.system(), platform.system().lower())

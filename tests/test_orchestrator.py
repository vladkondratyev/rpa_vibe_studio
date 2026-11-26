from __future__ import annotations

import datetime as dt
import os
import tempfile

import pytest

_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["RPA_DATABASE_URL"] = f"sqlite:///{_db.name}"
os.environ["RPA_TOKEN"] = "test-token"

from fastapi.testclient import TestClient  # noqa: E402

from orchestrator import services  # noqa: E402
from orchestrator.main import app  # noqa: E402
from orchestrator.models import Job, Robot, Schedule, SessionLocal  # noqa: E402
from rpa_robot.agent import Agent  # noqa: E402

AUTH = {"Authorization": "Bearer test-token"}

DEFINITION = {
    "variables": [{"name": "n", "default": 2}],
    "outputs": ["result"],
    "nodes": [
        {"id": "s", "type": "core.start", "params": {}},
        {"id": "p", "type": "core.python", "params": {"code": "result = n * 10\nlog(f'считаю {n}')"}},
        {"id": "e", "type": "core.end", "params": {}},
    ],
    "edges": [{"source": "s", "sourceHandle": "out", "target": "p"},
              {"source": "p", "sourceHandle": "out", "target": "e"}],
}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        c.headers.update(AUTH)
        yield c


def make_agent(client, name="robot-1", tags=("web",)):
    agent = Agent("http://testserver", name, list(tags))
    agent.http = client  # TestClient совместим с httpx.Client
    agent.heartbeat()
    return agent


def claim(client, name):
    r = client.post("/api/agent/claim", json={"name": name})
    return None if r.status_code == 204 else r.json()


def test_auth_required(client):
    assert TestClient(app).get("/api/processes").status_code == 401
    assert client.get("/api/health").json()["auth"] is True


def test_catalog(client):
    types = {b["type"] for b in client.get("/api/blocks").json()}
    assert {"core.start", "web.click", "excel.read_table", "files.copy"} <= types


def test_process_crud_versions_and_validation(client):
    r = client.post("/api/processes", json={"name": "Пустой"})
    assert r.status_code == 201
    p = r.json()
    assert p["version"] == 1 and client.post(f"/api/processes/{p['id']}/validate").json()["ok"]
    assert client.post("/api/processes", json={"name": "Пустой"}).status_code == 409
    bad = {"nodes": [{"id": "x", "type": "core.log", "params": {}}], "edges": []}
    p2 = client.put(f"/api/processes/{p['id']}", json={"name": "Пустой", "definition": bad}).json()
    assert p2["version"] == 2
    assert client.post(f"/api/processes/{p['id']}/run", json={}).status_code == 422
    assert client.delete(f"/api/processes/{p['id']}").status_code == 204


def test_full_cycle_with_robot(client):
    p = client.post("/api/processes", json={"name": "Умножение", "definition": DEFINITION}).json()
    job = client.post(f"/api/processes/{p['id']}/run", json={"inputs": {"n": 7}}).json()
    assert job["status"] == "queued"

    agent = make_agent(client)
    robots = client.get("/api/robots").json()
    assert robots[0]["online"] and "os=" + robots[0]["os"] in robots[0]["tags"]

    claimed = claim(client, "robot-1")
    assert claimed["id"] == job["id"]
    assert client.get(f"/api/jobs/{job['id']}").json()["status"] == "running"
    agent.execute(claimed)

    done = client.get(f"/api/jobs/{job['id']}").json()
    assert done["status"] == "success", done["error"]
    assert done["outputs"] == {"result": 70} and done["executed_by"] == "robot-1"
    messages = [l["message"] for l in client.get(f"/api/jobs/{job['id']}/logs").json()]
    assert "считаю 7" in messages and "Процесс завершён успешно" in messages
    assert claim(client, "robot-1") is None


def test_tags_and_target_robot(client):
    p = client.post("/api/processes", json={"name": "Только Linux", "definition": DEFINITION}).json()
    make_agent(client, "win-1", ["excel"])
    job = client.post(f"/api/processes/{p['id']}/run", json={"tags": ["gpu"]}).json()
    assert claim(client, "win-1") is None  # нет метки gpu
    make_agent(client, "gpu-1", ["gpu"])
    assert claim(client, "gpu-1")["id"] == job["id"]
    client.post(f"/api/agent/jobs/{job['id']}/finish", json={"status": "success"})

    job2 = client.post(f"/api/processes/{p['id']}/run", json={"robot": "win-1"}).json()
    assert claim(client, "gpu-1") is None
    assert claim(client, "win-1")["id"] == job2["id"]
    client.post(f"/api/agent/jobs/{job2['id']}/finish", json={"status": "success"})


def test_cancel_queued_and_running(client):
    p = client.post("/api/processes", json={"name": "Долгий", "definition": {
        "nodes": [{"id": "s", "type": "core.start"}, {"id": "d", "type": "core.delay", "params": {"seconds": 30}}],
        "edges": [{"source": "s", "target": "d"}]}}).json()
    q = client.post(f"/api/processes/{p['id']}/run", json={}).json()
    assert client.post(f"/api/jobs/{q['id']}/cancel").json()["status"] == "cancelled"

    agent = make_agent(client, "robot-c")
    job = client.post(f"/api/processes/{p['id']}/run", json={"robot": "robot-c"}).json()
    claimed = claim(client, "robot-c")
    assert client.post(f"/api/jobs/{job['id']}/cancel").json()["cancel_requested"] is True
    agent.execute(claimed)  # отмену получит через ответ на отправку журнала
    assert client.get(f"/api/jobs/{job['id']}").json()["status"] == "cancelled"


def test_failed_job_reports_node(client):
    bad = {"nodes": [{"id": "s", "type": "core.start"},
                     {"id": "boom", "type": "core.fail", "params": {"message": "плохие данные"}}],
           "edges": [{"source": "s", "target": "boom"}]}
    p = client.post("/api/processes", json={"name": "Падает", "definition": bad}).json()
    agent = make_agent(client, "robot-f")
    job = client.post(f"/api/processes/{p['id']}/run", json={"robot": "robot-f"}).json()
    agent.execute(claim(client, "robot-f"))
    j = client.get(f"/api/jobs/{job['id']}").json()
    assert j["status"] == "failed" and j["failed_node"] == "boom" and "плохие данные" in j["error"]


def test_schedule_creates_jobs(client):
    p = client.post("/api/processes", json={"name": "По расписанию", "definition": DEFINITION}).json()
    assert client.post("/api/schedules", json={"process_id": p["id"], "cron": "не cron"}).status_code == 422
    s = client.post("/api/schedules", json={"process_id": p["id"], "cron": "*/5 * * * *",
                                            "inputs": {"n": 3}}).json()
    assert s["next_run_at"]
    db = SessionLocal()
    sch = db.get(Schedule, s["id"])
    sch.next_run_at = services.utcnow() - dt.timedelta(seconds=1)
    db.commit()
    assert services.run_due_schedules(db) == 1
    db.close()
    jobs = client.get("/api/jobs", params={"process_id": p["id"]}).json()
    assert jobs[0]["source"] == "schedule" and jobs[0]["inputs"] == {"n": 3}
    client.post(f"/api/jobs/{jobs[0]['id']}/cancel")


def test_lost_robot_fails_job(client):
    p = client.post("/api/processes", json={"name": "Потерянный", "definition": DEFINITION}).json()
    make_agent(client, "robot-lost")
    job = client.post(f"/api/processes/{p['id']}/run", json={"robot": "robot-lost"}).json()
    claim(client, "robot-lost")
    db = SessionLocal()
    robot = db.query(Robot).filter_by(name="robot-lost").one()
    robot.last_seen = services.utcnow() - dt.timedelta(minutes=10)
    db.commit()
    assert services.fail_lost_jobs(db) == 1
    assert db.get(Job, job["id"]).status == "failed"
    db.close()


def test_artifact_with_cyrillic_name(client):
    p = client.post("/api/processes", json={"name": "Артефакт", "definition": DEFINITION}).json()
    job = client.post(f"/api/processes/{p['id']}/run", json={}).json()
    make_agent(client, "robot-art")
    claim(client, "robot-art")
    a = client.post(f"/api/agent/jobs/{job['id']}/artifacts",
                    json={"name": "ошибка входа.png", "data_b64": "iVBORw0KGgo="}).json()
    r = client.get(f"/api/artifacts/{a['id']}")
    assert r.status_code == 200 and r.content == b"\x89PNG\r\n\x1a\n"
    assert "filename*=UTF-8''%D0%BE" in r.headers["content-disposition"]

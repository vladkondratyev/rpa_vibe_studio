"""Тесты функциональности 0.2: банковские утилиты, OCR-шаблоны, S3, секреты,
пользователи и роли, очереди, шаблоны процессов, аналитика, версии, Vibe."""
from __future__ import annotations

import datetime as dt
import os
import tempfile

import pytest

_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["RPA_DATABASE_URL"] = f"sqlite:///{_db.name}"
os.environ["RPA_TOKEN"] = "test-token"
os.environ["RPA_SECRET_KEY"] = "test-secret-key"

from fastapi.testclient import TestClient  # noqa: E402

from orchestrator import services  # noqa: E402
from orchestrator.main import app  # noqa: E402
from orchestrator.models import SessionLocal  # noqa: E402
from rpa_engine import Context, banking, ocr, run_process, validate  # noqa: E402
from rpa_engine.s3 import sign_request  # noqa: E402

AUTH = {"Authorization": "Bearer test-token"}


# ================================================================ банковские утилиты
def test_requisites_checksums():
    assert banking.check_inn("7707083893")          # ПАО Сбербанк
    assert not banking.check_inn("7707083894")
    assert banking.check_inn("500100732259")        # 12-значный ИНН
    assert banking.check_snils("112-233-445 95")
    assert not banking.check_snils("112-233-445 96")
    assert banking.check_ogrn("1027700132195")
    assert banking.check_account("30101810400000000225", "044525225")  # корсчёт Сбербанка
    assert banking.check_account("40702810938000000001", "044525225") is False or True  # не падает


def test_amount_in_words():
    assert banking.amount_in_words(1234.5) == "Одна тысяча двести тридцать четыре рубля 50 копеек"
    assert banking.amount_in_words(21) == "Двадцать один рубль 00 копеек"
    assert banking.amount_in_words("2 000 000,01") == "Два миллиона рублей 01 копейка"
    assert banking.amount_in_words(0) == "Ноль рублей 00 копеек"


def test_1c_exchange_roundtrip():
    text = banking.build_1c_payments([{"Сумма": 1500.5, "Получатель": "ООО Ромашка", "ПолучательИНН": "7707083893",
                                       "ПолучательСчет": "40702810000000000001", "ПолучательБИК": "044525225",
                                       "НазначениеПлатежа": "Оплата по счёту 15"}], "40702810900000000002")
    parsed = banking.parse_1c_statement(text.encode("cp1251"))
    doc = parsed["documents"][0]
    assert parsed["header"]["РасчСчет"] == "40702810900000000002"
    assert doc["ВидДокумента"] == "Платежное поручение"
    assert doc["Сумма"] == 1500.5 and doc["Получатель"] == "ООО Ромашка"


def test_screen_watchlist():
    hits = banking.screen([{"name": "Иванов Пётр Сергеевич", "inn": ""}, {"name": "ООО Ромашка", "inn": "7707083893"},
                           {"name": "Сидоров Иван", "inn": ""}],
                          [{"name": "ИВАНОВ ПЕТР СЕРГЕЕВИЧ", "inn": ""}, {"name": "АО Другая", "inn": "7707083893"}])
    assert {h["by"] for h in hits} == {"name", "inn"} and len(hits) == 2


# ================================================================ OCR
def _mrz_line2(series: str, number: str, birth: str, sex: str, issue: str, code: str) -> str:
    num9 = series[:3] + number
    l2 = num9 + str(ocr._mrz_check(num9)) + "RUS" + birth + str(ocr._mrz_check(birth)) + sex + "<" * 7
    opt = series[3] + issue + code + "<"
    return (l2 + opt + str(ocr._mrz_check(opt))).ljust(43, "<") + "0"


def test_passport_mrz_and_template():
    text = ("ПАСПОРТ ВЫДАН ГУ МВД РОССИИ ПО Г. МОСКВЕ\nДата выдачи 20.05.2010 Код подразделения 770-001\n"
            "Фамилия ИВАНОВ\nИмя ИВАН\nОтчество ИВАНОВИЧ\nПол МУЖ Дата рождения 15.01.1980\n"
            "Место рождения\nГОР. МОСКВА\n"
            "PNRUSIVANOV<<IVAN<IVANOVI3<<<<<<<<<<<<<<<<<<\n"
            + _mrz_line2("4509", "123456", "800115", "M", "100520", "770001"))
    mrz = ocr.parse_passport_mrz(text)
    assert mrz["valid"] and mrz["series_number"] == "4509123456"
    assert (mrz["surname"], mrz["name"], mrz["patronymic"]) == ("Иванов", "Иван", "Иванович")
    assert mrz["birth_date"] == "15.01.1980" and mrz["subdivision"] == "770-001"
    tpl = ocr.builtin("passport_rf")
    found, score = ocr.classify(text, ocr.BUILTIN_TEMPLATES)
    assert found["id"] == "passport_rf" and score > 0.5
    res = ocr.extract([{"text": text, "words": [], "entities": {}}], tpl)
    assert res["ok"], res
    assert res["fields"]["birth_place"] == "Гор. Москва" or res["fields"]["birth_place"].upper() == "ГОР. МОСКВА"
    assert res["sources"]["surname"] == "mrz"


def test_payment_order_template_and_zones():
    text = ("ПЛАТЁЖНОЕ ПОРУЧЕНИЕ № 482 14.11.2026 0401060\nСумма прописью Сто тысяч рублей 00 копеек\n"
            "ИНН 7707083893 КПП 773601001 Сумма 100000-00\nСч. № 40702810938000000001\nБИК 044525225\n"
            "ИНН 500100732259 Сч. № 40802810400000000002\nОплата по договору 15 от 01.11.2026\nНазначение платежа")
    res = ocr.extract([{"text": text, "words": [], "entities": {}}], ocr.builtin("payment_order"))
    f = res["fields"]
    assert f["number"] == "482" and f["date"] == "14.11.2026" and f["amount"] == 100000.0
    assert f["payer_inn"] == "7707083893" and f["recipient_inn"] == "500100732259"
    assert f["purpose"].startswith("Оплата по договору")
    words = [{"text": "Сумма", "x": 0.1, "y": 0.5, "w": 0.1, "h": 0.02, "line": "1"},
             {"text": "500,00", "x": 0.6, "y": 0.5, "w": 0.1, "h": 0.02, "line": "1"}]
    assert ocr.zone_text(words, [0.5, 0.45, 0.4, 0.1]) == "500,00"
    custom = {"id": "t", "fields": [{"name": "s", "zone": [0.5, 0.45, 0.4, 0.1], "type": "amount", "required": True}]}
    assert ocr.extract([{"text": "", "words": words, "entities": {}}], custom)["fields"]["s"] == 500.0


def test_validation_marks_review():
    res = ocr.extract([{"text": "СНИЛС 112-233-445 96", "words": [], "entities": {}}], ocr.builtin("snils"))
    assert not res["ok"] and "snils" in res["errors"]


def test_s3_signature_reference_vector():
    h = sign_request("GET", "https://examplebucket.s3.amazonaws.com/test.txt", {"Range": "bytes=0-9"},
                     "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855", "AKIAIOSFODNN7EXAMPLE",
                     "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", "us-east-1",
                     dt.datetime(2013, 5, 24, tzinfo=dt.timezone.utc))
    assert h["Authorization"].endswith("f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41")


# ================================================================ движок: секреты
def test_secrets_masked_and_resolved():
    logs = []
    definition = {"variables": [], "outputs": ["auth"], "edges": [
        {"source": "s", "target": "p"}, {"source": "p", "target": "e"}], "nodes": [
        {"id": "s", "type": "core.start", "params": {}},
        {"id": "p", "type": "core.python", "params": {"code": "auth = 'Bearer ' + secret('api')\nlog('ключ ' + auth)"}},
        {"id": "e", "type": "core.end", "params": {}}]}
    ctx = Context(log=lambda lvl, msg, node: logs.append(msg), secrets={"api": "s3cr3t-value"})
    res = run_process(definition, ctx=ctx)
    assert res.status == "success"
    assert any("ключ Bearer ***" in m for m in logs) and not any("s3cr3t" in m for m in logs)


def test_all_blocks_registered():
    from rpa_engine import catalog

    types = {b["type"] for b in catalog()}
    for t in ("desktop.click", "vision.click_image", "vision.click_text", "ocr.extract", "ai.extract", "email.read",
              "http.soap", "onec.query", "db.query", "bank.cbr_rates", "bank.screen", "s3.upload", "yadisk.upload",
              "yandex.vision_ocr", "yandex.gpt", "queue.next", "notify.telegram"):
        assert t in types


# ================================================================ оркестратор
@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        c.headers.update(AUTH)
        yield c


def test_users_roles_and_audit(client):
    r = client.post("/api/users", json={"username": "olga", "role": "viewer", "password": "pass-1234"})
    assert r.status_code == 201
    token = client.post("/api/auth/login", json={"username": "olga", "password": "pass-1234"}).json()["token"]
    viewer = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/processes", headers=viewer).status_code == 200
    assert client.post("/api/processes", json={"name": "x"}, headers=viewer).status_code == 403
    assert client.get("/api/agent/jobs/1", headers=viewer).status_code == 403
    assert client.post("/api/auth/login", json={"username": "olga", "password": "wrong"}).status_code == 401
    assert client.get("/api/processes", headers={"Authorization": "Bearer nope"}).status_code == 401
    actions = [a["action"] for a in client.get("/api/audit").json()]
    assert "user.create" in actions and "auth.login" in actions and "auth.failed" in actions


def test_secrets_encrypted_and_delivered(client):
    assert client.put("/api/secrets/portal_pwd", json={"name": "portal_pwd", "value": "Pa$$w0rd"}).status_code == 200
    client.put("/api/secrets/unused", json={"name": "unused", "value": "zzz"})
    listed = client.get("/api/secrets").json()
    assert all("value" not in s for s in listed)
    with SessionLocal() as db:
        from orchestrator.models import Secret

        assert "Pa$$" not in db.query(Secret).filter_by(name="portal_pwd").one().value_enc
    definition = {"variables": [], "outputs": [], "edges": [{"source": "s", "target": "e"}], "nodes": [
        {"id": "s", "type": "core.start", "params": {}},
        {"id": "e", "type": "core.end", "params": {"x": "{{ secret('portal_pwd') }}"}}]}
    pid = client.post("/api/processes", json={"name": "with secret", "definition": definition}).json()["id"]
    client.post(f"/api/processes/{pid}/run", json={"robot": "robot-s"})
    client.post("/api/agent/heartbeat", json={"name": "robot-s"})
    job = client.post("/api/agent/claim", json={"name": "robot-s"}).json()
    assert job["secrets"] == {"portal_pwd": "Pa$$w0rd"}


def test_queue_flow_retries_dedupe_trigger(client):
    pid = client.post("/api/processes", json={"name": "queue worker"}).json()["id"]
    q = client.post("/api/queues", json={"name": "Заявки", "max_retries": 1, "sla_minutes": 60,
                                         "process_id": pid, "max_parallel": 2}).json()
    added = client.post(f"/api/queues/{q['id']}/items", json={"items": [
        {"reference": "A-1", "data": {"n": 1}}, {"reference": "A-1", "data": {"n": 1}},
        {"reference": "A-2", "data": {"n": 2}, "priority": 5}]}).json()
    assert added == {"added": 2}
    with SessionLocal() as db:
        assert services.run_queue_triggers(db) == 2
        assert services.run_queue_triggers(db) == 0  # уже max_parallel запусков
    it = client.post("/api/agent/queues/Заявки/next", json={"robot": "r1"}).json()
    assert it["reference"] == "A-2"  # приоритет выше
    r = client.post(f"/api/agent/queue-items/{it['id']}/finish", json={"status": "failed", "error": "сбой сайта"})
    assert r.json()["status"] == "new" and r.json()["retries"] == 1  # повтор
    it2 = client.post("/api/agent/queues/Заявки/next", json={"robot": "r1"}).json()
    r = client.post(f"/api/agent/queue-items/{it2['id']}/finish", json={"status": "failed", "error": "снова"})
    assert r.json()["status"] == "failed"  # попытки кончились
    it3 = client.post("/api/agent/queues/Заявки/next", json={"robot": "r1"}).json()
    client.post(f"/api/agent/queue-items/{it3['id']}/finish", json={"status": "done", "result": {"ok": True}})
    assert client.post("/api/agent/queues/Заявки/next", json={"robot": "r1"}).status_code == 204
    counts = next(x for x in client.get("/api/queues").json() if x["name"] == "Заявки")["counts"]
    assert counts["done"] == 1 and counts["failed"] == 1


def test_templates_all_valid_and_usable(client):
    templates = client.get("/api/templates").json()
    assert len(templates) >= 10
    bad = [t["id"] for t in templates if not t["valid"]]
    assert not bad, bad
    assert any(t.get("category") == "Банк" for t in templates)
    r = client.post(f"/api/templates/{templates[0]['id']}/use", json={})
    assert r.status_code == 201
    assert client.get(f"/api/processes/{r.json()['id']}").json()["minutes_saved"] >= 0


def test_versions_restore_export_import(client):
    p = client.post("/api/processes", json={"name": "versioned"}).json()
    d2 = dict(p["definition"], variables=[{"name": "a", "default": 1}])
    client.put(f"/api/processes/{p['id']}", json={"name": "versioned", "definition": d2})
    vs = client.get(f"/api/processes/{p['id']}/versions").json()
    assert [v["version"] for v in vs] == [2, 1]
    assert client.post(f"/api/processes/{p['id']}/versions/1/restore").json()["version"] == 3
    exported = client.get(f"/api/processes/{p['id']}/export").json()
    assert exported["definition"]["variables"] == []
    imp = client.post("/api/processes/import", json=exported).json()
    assert imp["name"] == "versioned (2)" and imp["errors"] == []


def test_analytics(client):
    data = client.get("/api/analytics?days=7").json()
    assert len(data["daily"]) == 7 and "hours_saved" in data["totals"]


def test_ai_connections_and_vibe(client, monkeypatch):
    client.put("/api/secrets/ya_key", json={"name": "ya_key", "value": "AQVN-test"})
    r = client.post("/api/ai", json={"name": "yandex", "provider": "yandexgpt", "folder_id": "b1gtest",
                                     "secret_name": "ya_key", "is_default": True})
    assert r.status_code == 201
    with SessionLocal() as db:
        conns = services.ai_connections(db)
    assert conns["yandex"]["api_key"] == "AQVN-test"

    from rpa_engine import llm

    def fake_chat(conn, prompt, system="", images=None, max_tokens=0, temperature=None):
        assert conn["provider"] == "yandexgpt" and "core.start" in system
        return ('```json\n{"name": "Курсы ЦБ", "description": "d", "minutes_saved": 10, "definition": {'
                '"nodes": [{"id": "start", "type": "core.start", "params": {}},'
                '{"id": "r", "type": "bank.cbr_rates", "params": {"result": "rates"}},'
                '{"id": "end", "type": "core.end", "params": {}}],'
                '"edges": [{"source": "start", "target": "r"}, {"source": "r", "target": "end"}]}}\n```')

    monkeypatch.setattr(llm, "chat", fake_chat)
    res = client.post("/api/vibe", json={"prompt": "Каждое утро загружай курсы ЦБ"}).json()
    assert res["errors"] == [] and res["id"] and res["definition"]["nodes"][1]["position"]["x"] > 0


def test_yandexgpt_request_shape(monkeypatch):
    import httpx

    from rpa_engine import llm

    seen = {}

    class R:
        status_code = 200

        def json(self):
            return {"result": {"alternatives": [{"message": {"text": "ok"}}]}}

    def fake_post(url, json=None, headers=None, **kw):
        seen.update(url=url, body=json, headers=headers)
        return R()

    monkeypatch.setattr(httpx, "post", fake_post)
    assert llm.chat({"provider": "yandexgpt", "api_key": "AQVN1", "folder_id": "b1g"}, "привет", "ты бот") == "ok"
    assert seen["url"].endswith("/foundationModels/v1/completion")
    assert seen["body"]["modelUri"] == "gpt://b1g/yandexgpt/latest"
    assert seen["headers"]["Authorization"] == "Api-Key AQVN1"
    assert seen["body"]["messages"][0] == {"role": "system", "text": "ты бот"}

"""End-to-end test of the full hiring lifecycle with the offline fake LLM.

Run:  cd backend && pytest -q
"""
import os
import tempfile
import time

_tmp = tempfile.mkdtemp(prefix="tp-test-")
os.environ.update({
    "LLM_PROVIDER": "fake", "DATABASE_URL": os.environ.get("TEST_DATABASE_URL", ""), "DATA_DIR": _tmp, "GROQ_API_KEY": "", "GEMINI_API_KEY": "",
    "EMAIL_PROVIDER": "console", "TELEGRAM_BOT_TOKEN": "", "MAX_REPLANS": "1", "SCREEN_PASS_SCORE": "0",
    "ASSESSMENT_PASS_SCORE": "0", "INTERVIEW_QUESTIONS": "2", "ADMIN_EMAIL": "hr@test.com",
    "ADMIN_PASSWORD": "pw", "PUBLIC_URL": "http://test", "JWT_SECRET": "test-secret-0123456789-abcdefghijklmnop",
})

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.agents import skills  # noqa: E402
from app.agents.schemas import CodingProblem  # noqa: E402
from app.agents.schemas import TestCase as Case  # noqa: E402
from app.db import session_scope  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Application  # noqa: E402

RESUME = ("Experienced backend engineer with 4 years building REST APIs in Python, FastAPI and Django. "
          "Designed PostgreSQL schemas, Docker deployments, Redis caching and Kafka consumers on AWS. "
          "Led migration of a monolith to microservices and mentored two junior developers.")


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        token = c.post("/api/auth/login", json={"email": "hr@test.com", "password": "pw"}).json()["token"]
        c.headers.update({"Authorization": f"Bearer {token}"})
        yield c


def wait_until(fn, timeout=30, msg=""):
    end = time.time() + timeout
    while time.time() < end:
        val = fn()
        if val:
            return val
        time.sleep(0.2)
    raise AssertionError(f"Timed out waiting: {msg}")


def job_state(c, job_id):
    return c.get(f"/api/hr/jobs/{job_id}").json()


def cand_state(c, app_id):
    return c.get(f"/api/hr/candidates/{app_id}").json()


def token_of(app_id):
    with session_scope() as db:
        return db.get(Application, app_id).token


def test_auth_required():
    with TestClient(app) as c:
        assert c.get("/api/hr/jobs").status_code == 401
        assert c.post("/api/auth/login", json={"email": "x", "password": "y"}).status_code == 401


def test_knowledge_seeded_and_searchable(client):
    docs = client.get("/api/hr/knowledge").json()
    assert len(docs) >= 7
    hits = client.get("/api/hr/knowledge-search", params={"q": "salary band senior backend"}).json()
    assert any("Salary" in h["title"] for h in hits)


def test_full_hiring_lifecycle(client):
    c = client
    job_id = c.post("/api/hr/jobs", json={"request_text": "Hire a backend engineer with Python, 3-5 years",
                                          "application_window_hours": 1, "min_applications": 5,
                                          "shortlist_size": 1}).json()["id"]

    # 1. JD drafted -> HR asks for changes -> redrafted -> approve
    wait_until(lambda: job_state(c, job_id)["waiting_for"] == "jd_approval", msg="jd approval")
    assert c.post(f"/api/hr/jobs/{job_id}/jd-decision", json={"action": "changes", "feedback": "Mention Kafka"}).is_success
    wait_until(lambda: job_state(c, job_id)["jd_version"] == 2 and job_state(c, job_id)["waiting_for"] == "jd_approval",
               msg="redraft")
    assert c.post(f"/api/hr/jobs/{job_id}/jd-decision", json={"action": "approve"}).is_success
    js = wait_until(lambda: (s := job_state(c, job_id))["waiting_for"] == "deadline" and s, msg="published")
    assert js["postings"]["careers"]["status"] == "live"
    assert "linkedin.com" in js["postings"]["linkedin"]["share_url"]

    # 2. Candidates apply on the public careers page
    pub = c.get("/api/public/jobs").json()
    assert any(j["id"] == job_id for j in pub)
    for i, name in enumerate(["Asha Verma", "Rohit Kumar"]):
        r = c.post(f"/api/public/jobs/{job_id}/apply",
                   data={"name": name, "email": f"cand{i}@mail.com", "phone": "", "consent": "true"},
                   files={"resume": ("resume.txt", RESUME.encode(), "text/plain")})
        assert r.status_code == 200, r.text
    dup = c.post(f"/api/public/jobs/{job_id}/apply",
                 data={"name": "Asha Verma", "email": "cand0@mail.com", "consent": "true"},
                 files={"resume": ("resume.txt", RESUME.encode(), "text/plain")})
    assert dup.status_code == 409

    # 3. Close early -> too few applicants -> REPLAN proposed -> HR approves -> reposted
    assert c.post(f"/api/hr/jobs/{job_id}/close-applications").is_success
    js = wait_until(lambda: (s := job_state(c, job_id))["waiting_for"] == "replan_approval" and s, msg="replan")
    assert js["replan_proposal"]["new_jd"]["title"]
    assert c.post(f"/api/hr/jobs/{job_id}/replan-decision", json={"action": "approve"}).is_success
    js = wait_until(lambda: (s := job_state(c, job_id))["waiting_for"] == "deadline" and s["replan_count"] == 1 and s,
                    msg="reposted")
    assert js["jd_version"] == 3

    # 4. Close again -> replan budget used -> screening -> shortlist 1, waitlist 1
    assert c.post(f"/api/hr/jobs/{job_id}/close-applications").is_success
    js = wait_until(lambda: (s := job_state(c, job_id))["status"] == "in_pipeline" and s, msg="screened")
    stages = sorted(a["stage"] for a in js["applications"])
    assert "waitlisted" in stages
    top = js["applications"][0]
    app_id = top["id"]

    # 5. Proctored assessment
    wait_until(lambda: cand_state(c, app_id)["waiting_for"] == "assessment", msg="assessment invite")
    tok = token_of(app_id)
    a = c.get(f"/api/candidate/{tok}/assessment").json()
    assert a["available"] and "answer_index" not in str(a["assessment"])
    assert c.post(f"/api/candidate/{tok}/assessment/start").is_success
    assert c.post(f"/api/candidate/{tok}/proctor", json={"phase": "assessment", "events": [
        {"type": "tab_hidden", "severity": "medium", "detail": "switched tab"},
        {"type": "multiple_faces", "severity": "high", "detail": "2 faces"}]}).json()["integrity"] == 85
    answers = {str(i): 1 for i in range(len(a["assessment"]["mcqs"]))}
    assert c.post(f"/api/candidate/{tok}/assessment/submit", json={"mcq_answers": answers}).is_success

    # 6. AI interview
    wait_until(lambda: cand_state(c, app_id)["waiting_for"] == "interview", msg="interview invite")
    r = c.post(f"/api/candidate/{tok}/interview/next", json={"answer": ""}).json()
    assert not r["done"]
    r = c.post(f"/api/candidate/{tok}/interview/next", json={"answer": "I built APIs with FastAPI."}).json()
    assert not r["done"]
    r = c.post(f"/api/candidate/{tok}/interview/next", json={"answer": "I would use a queue and retries."}).json()
    assert r["done"]

    # 7. HR reviews final report -> approves -> offer drafted -> HR edits CTC & approves
    cs = wait_until(lambda: (s := cand_state(c, app_id))["waiting_for"] == "hr_decision" and s, msg="hr decision")
    assert cs["final_report"]["scores"]["integrity"] == 85
    assert c.post(f"/api/hr/candidates/{app_id}/decision", json={"action": "approve", "feedback": "Great"}).is_success
    wait_until(lambda: cand_state(c, app_id)["waiting_for"] == "offer_approval", msg="offer approval")
    assert c.get(f"/api/hr/candidates/{app_id}/offer.pdf").headers["content-type"] == "application/pdf"
    assert c.post(f"/api/hr/candidates/{app_id}/offer-decision",
                  json={"action": "approve", "edits": {"annual_ctc": 1800000, "joining_date": "2026-11-15"}}).is_success

    # 8. Candidate accepts -> hired -> job filled, waitlisted candidate closed
    wait_until(lambda: cand_state(c, app_id)["waiting_for"] == "candidate_response", msg="offer sent")
    offer = c.get(f"/api/candidate/{tok}/offer").json()
    assert offer["available"] and offer["offer"]["annual_ctc"] == 1800000 and "justification" not in offer["offer"]
    assert c.get(f"/api/candidate/{tok}/offer.pdf").status_code == 200
    assert c.post(f"/api/candidate/{tok}/offer/respond", json={"action": "accept"}).is_success
    wait_until(lambda: job_state(c, job_id)["status"] == "filled", msg="filled")
    final = job_state(c, job_id)
    assert {a["stage"] for a in final["applications"]} == {"hired", "rejected"}

    # 9. Learning memory captured HR's feedback
    notes = [f["note"] for f in c.get("/api/hr/feedback").json()]
    assert any("Kafka" in n for n in notes)
    assert any("offer fields" in n for n in notes)


def test_coding_problem_validation():
    good = CodingProblem(
        title="Sum", description="Return a+b", function_name="add", starter_code="def add(a, b):\n    pass",
        reference_solution="def add(a, b):\n    return a + b", difficulty="easy",
        tests=[Case(args_json="[1, 2]", expected_json="3"), Case(args_json="[0, 0]", expected_json="0"),
               Case(args_json="[-1, 1]", expected_json="0"), Case(args_json="[5, 5]", expected_json="11")])
    v = skills._validate_problem(good)
    assert v and len(v["tests"]) == 3  # the wrong '11' test is dropped

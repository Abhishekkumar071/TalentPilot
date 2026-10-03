"""Time-based triggers. Runs every minute inside the app, and can also be hit from
an external free cron (cron-job.org / GitHub Actions) via /api/cron/tick so
deadlines fire even after the free host has gone to sleep."""
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select

from .agents import runtime, skills
from .db import session_scope
from .models import Application, Job, JobStatus, Stage
from .utils import as_utc, log_event, now

log = logging.getLogger(__name__)
_intake = ThreadPoolExecutor(max_workers=2, thread_name_prefix="intake")


def tick() -> dict:
    fired = {"job_deadlines": 0, "expired": 0}
    t = now()
    with session_scope() as db:
        jobs = db.execute(select(Job.id, Job.deadline).where(Job.status == JobStatus.COLLECTING)).all()
        apps = db.scalars(select(Application).where(
            Application.stage.in_([Stage.ASSESSMENT_SENT, Stage.INTERVIEW_SENT, Stage.OFFER_SENT]),
            Application.stage_deadline.is_not(None))).all()
        due_apps = []
        for a in apps:
            deadline = as_utc(a.stage_deadline)
            if a.stage == Stage.ASSESSMENT_SENT and a.assessment_started_at:  # let an in-progress test finish
                minutes = (a.job.assessment or {}).get("duration_minutes", 30) + 10
                deadline = max(deadline, as_utc(a.assessment_started_at) + timedelta(minutes=minutes))
            if a.stage == Stage.INTERVIEW_SENT and a.interview_transcript:
                deadline = deadline + timedelta(hours=1)
            if deadline <= t:
                due_apps.append(a.id)

    for job_id, deadline in jobs:
        if deadline and as_utc(deadline) <= t and runtime.job_waiting_for(job_id) == "deadline":
            runtime.resume_job(job_id, {"action": "deadline"})
            fired["job_deadlines"] += 1
    for app_id in due_apps:
        if runtime.candidate_waiting_for(app_id) in ("assessment", "interview", "candidate_response"):
            runtime.resume_candidate(app_id, {"action": "expired"})
            fired["expired"] += 1
    return fired


async def loop(interval: int = 60) -> None:
    while True:
        await asyncio.sleep(interval)
        try:
            fired = await run_in_threadpool(tick)
            if any(fired.values()):
                log.info("Scheduler fired: %s", fired)
        except Exception:
            log.exception("Scheduler tick failed")


# ------------------------------------------------------------------ intake
def _score_new_application(app_id: int) -> None:
    try:
        with session_scope() as db:
            app = db.get(Application, app_id)
            job = app.job
            result = skills.score_application(db, app, job)
            app.screening, app.score, app.scored_version = result, result["total"], job.jd_version
            app.stage = Stage.SCREENED
            log_event(db, f"Resume screened: {app.name} scored {result['total']}", actor="screening-agent",
                      job_id=job.id, application_id=app.id)
    except Exception as exc:
        log.exception("Background scoring failed for %s", app_id)
        with session_scope() as db:
            db.get(Application, app_id).last_error = f"Scoring failed (will retry at deadline): {exc}"


def score_in_background(app_id: int) -> None:
    _intake.submit(_score_new_application, app_id)

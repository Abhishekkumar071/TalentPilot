"""Job-level agent workflow (one LangGraph thread per job).

  START ─► draft_jd ─► review_jd ⏸ ─┬─ approve ─► publish ─► wait_deadline ⏸ ─► evaluate ─┬─ enough ─► screen ─► END
              ▲                     ├─ changes ──┘ (redraft)        ▲                    │
              │                     └─ cancel ─► cancel             │                    └─ too few ─► replan ─► review_replan ⏸
              │                                                     │                                  │ approve ─► apply_replan ─┘
  START(entry=replan) ─────────────────────────────────────────────────────────────────► replan        │ changes ─► replan
                                                                                                       └ reject  ─► screen
⏸ = LangGraph interrupt(): the graph is checkpointed to the database and waits
(for hours or days) until HR clicks a button or the deadline scheduler resumes it.
"""
import logging
from datetime import timedelta
from typing import TypedDict
from urllib.parse import quote

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from sqlalchemy import select

from ..config import settings
from ..db import session_scope
from ..models import Application, Job, JobStatus, Stage
from ..notify import notify_candidate, notify_hr, telegram
from ..utils import log_event, now
from . import skills
from .schemas import HiringRequest

log = logging.getLogger(__name__)
AGENT = "job-agent"


class JobState(TypedDict, total=False):
    job_id: int
    entry: str  # "new" | "replan"
    decision: str
    feedback: str | None


def _set_status(job_id: int, status: str, message: str | None = None, kind: str = "info", data: dict | None = None):
    with session_scope() as db:
        job = db.get(Job, job_id)
        job.status = status
        if message:
            log_event(db, message, actor=AGENT, kind=kind, job_id=job_id, data=data)


# ------------------------------------------------------------------ nodes
def draft_jd(state: JobState) -> JobState:
    job_id = state["job_id"]
    _set_status(job_id, JobStatus.DRAFTING, "Drafting job description from the hiring request using company knowledge (RAG)")
    with session_scope() as db:
        job = db.get(Job, job_id)
        feedback = state.get("feedback")
        overrides = {k: v for k, v in {"location": job.location, "work_mode": job.work_mode,
                                       "department": job.department}.items() if v}
        if feedback and job.jd:
            req = HiringRequest(**{k: job.jd.get(k) for k in ("title", "department", "location", "work_mode",
                                                               "employment_type", "experience_min", "experience_max")},
                                openings=job.openings, key_skills=job.jd.get("must_have_skills", []), notes="")
        else:
            req = skills.parse_request(job.request_text, overrides)
        jd, sources = skills.draft_jd(db, req, feedback, job.jd if feedback else None)
        previous = job.jd

    jd_dict = jd.model_dump()
    with session_scope() as db:
        job = db.get(Job, job_id)
        job.jd = jd_dict
        job.jd_markdown = skills.jd_to_markdown(jd_dict)
        job.jd_sources = sources
        job.jd_version += 1
        job.title, job.department, job.location = jd.title, jd.department, jd.location
        job.work_mode, job.employment_type = jd.work_mode, jd.employment_type
        if not feedback:
            job.openings = max(1, req.openings or job.openings)
        job.status = JobStatus.AWAITING_JD_APPROVAL
        log_event(db, f"{'Revised' if previous else 'Drafted'} JD '{jd.title}' grounded in {len(sources)} company "
                      "knowledge snippets", actor=AGENT, job_id=job_id, data={"sources": sources})
        log_event(db, "JD is ready - waiting for HR approval", actor=AGENT, kind="action_needed", job_id=job_id)
    notify_hr(f"JD ready for review: {jd.title}", f"/hr/jobs/{job_id}")
    return {"decision": "", "feedback": None}


def review_jd(state: JobState) -> JobState:
    answer = interrupt({"type": "jd_approval", "job_id": state["job_id"]})
    return {"decision": answer.get("action", "approve"), "feedback": answer.get("feedback")}


def publish(state: JobState) -> JobState:
    job_id = state["job_id"]
    _set_status(job_id, JobStatus.PUBLISHING, "Publishing job to all channels")
    with session_scope() as db:
        job = db.get(Job, job_id)
        jd = dict(job.jd)
        need_assessment = not job.assessment or job.assessment.get("jd_version") != job.jd_version
        version, window, reposted = job.jd_version, job.application_window_hours, bool(job.postings)
        assessment = skills.generate_assessment(db, jd) if need_assessment else None

    url = f"{settings.PUBLIC_URL}/jobs/{job_id}"
    linkedin_text = skills.linkedin_post(jd, url)
    channel_msg = (f"📢 <b>{'[Updated] ' if reposted else ''}We're hiring: {jd['title']}</b>\n"
                   f"🏢 {settings.COMPANY_NAME} · 📍 {jd.get('location')} ({jd.get('work_mode')})\n"
                   f"🧑‍💻 {jd.get('experience_min')}-{jd.get('experience_max')} yrs · "
                   f"{', '.join(jd.get('must_have_skills', [])[:5])}\n\nApply: {url}")
    tg_ok = telegram(channel_msg, settings.TELEGRAM_CHANNEL_ID) if settings.TELEGRAM_CHANNEL_ID else False
    ts = now().isoformat()
    postings = {
        "careers": {"status": "live", "url": url, "posted_at": ts},
        "linkedin": {"status": "ready", "text": linkedin_text, "posted_at": ts,
                     "share_url": f"https://www.linkedin.com/sharing/share-offsite/?url={quote(url, safe='')}",
                     "note": "LinkedIn Jobs API needs partner access - one-click share + ready-made post instead."},
        "naukri": {"status": "ready", "text": skills.naukri_text(jd, url), "posted_at": ts,
                   "note": "Naukri has no public API - formatted text ready to paste in the recruiter panel."},
        "telegram": {"status": "live" if tg_ok else ("failed" if settings.TELEGRAM_CHANNEL_ID else "not_configured"),
                     "posted_at": ts},
    }
    with session_scope() as db:
        job = db.get(Job, job_id)
        if assessment is not None:
            job.assessment = {**assessment, "jd_version": version}
        job.postings = postings
        job.deadline = now() + timedelta(hours=window)
        job.status = JobStatus.COLLECTING
        live = [k for k, v in postings.items() if v["status"] == "live"]
        log_event(db, f"{'Re-posted' if reposted else 'Posted'} on {', '.join(live)}; LinkedIn/Naukri content ready. "
                      f"Applications close {job.deadline:%d %b %H:%M} UTC", actor=AGENT, job_id=job_id)
        if assessment is not None:
            log_event(db, f"Generated assessment: {len(assessment['mcqs'])} MCQs, {len(assessment['coding'])} "
                          "coding problem(s) (tests verified against a reference solution)", actor=AGENT, job_id=job_id)
    notify_hr(f"Job is live: {jd['title']}", f"/jobs/{job_id}")
    return {"decision": "", "feedback": None}


def wait_deadline(state: JobState) -> JobState:
    answer = interrupt({"type": "deadline", "job_id": state["job_id"]})
    return {"decision": answer.get("action", "deadline")}


def _ensure_scored(job_id: int) -> None:
    """Score every application against the current JD version (one short DB session each)."""
    with session_scope() as db:
        ids = db.scalars(select(Application.id).join(Job).where(
            Application.job_id == job_id, Application.scored_version != Job.jd_version,
            Application.stage.in_([Stage.APPLIED, Stage.SCREENED]))).all()
    for app_id in ids:
        try:
            with session_scope() as db:
                app, job = db.get(Application, app_id), db.get(Job, job_id)
                result = skills.score_application(db, app, job)
                app.screening, app.score, app.scored_version = result, result["total"], job.jd_version
                app.stage = Stage.SCREENED
        except Exception as exc:
            log.exception("Scoring application %s failed", app_id)
            with session_scope() as db:
                db.get(Application, app_id).last_error = f"Scoring failed: {exc}"


def _metrics(job_id: int) -> dict:
    with session_scope() as db:
        job = db.get(Job, job_id)
        apps = [a for a in job.applications if a.stage in (Stage.APPLIED, Stage.SCREENED)]
        scores = [a.score for a in apps if a.score is not None]
        qualified = [s for s in scores if s >= settings.SCREEN_PASS_SCORE]
        return {"applications": len(apps), "scored": len(scores), "qualified": len(qualified),
                "avg_score": round(sum(scores) / len(scores), 1) if scores else 0,
                "min_applications": job.min_applications, "openings": job.openings,
                "pass_score": settings.SCREEN_PASS_SCORE, "replans_used": job.replan_count,
                "hours_open": job.application_window_hours}


def evaluate(state: JobState) -> JobState:
    job_id = state["job_id"]
    if state.get("decision") == "cancel":
        return {"decision": "cancel"}
    _set_status(job_id, JobStatus.EVALUATING, "Application window closed - scoring all resumes")
    _ensure_scored(job_id)
    m = _metrics(job_id)
    with session_scope() as db:
        job = db.get(Job, job_id)
        weak = m["applications"] < m["min_applications"] or m["qualified"] < m["openings"]
        decision = "replan" if weak and job.replan_count < settings.MAX_REPLANS else "screen"
        log_event(db, f"Pipeline check: {m['applications']} applications, {m['qualified']} qualified "
                      f"(avg score {m['avg_score']}). Decision: {decision}", actor=AGENT, job_id=job_id, data=m)
    return {"decision": decision}


def replan(state: JobState) -> JobState:
    job_id = state["job_id"]
    _set_status(job_id, JobStatus.REPLANNING, "Too few qualified applicants - replanning the job posting")
    _ensure_scored(job_id)
    m = _metrics(job_id)
    with session_scope() as db:
        job = db.get(Job, job_id)
        gaps = [g for a in job.applications if a.screening for g in a.screening.get("gaps", [])]
        proposal = skills.propose_replan(db, job, m, gaps, state.get("feedback"))
    with session_scope() as db:
        job = db.get(Job, job_id)
        job.replan_proposal = {**proposal.model_dump(), "metrics": m, "created_at": now().isoformat()}
        job.status = JobStatus.AWAITING_REPLAN_APPROVAL
        log_event(db, f"Replan proposed ({proposal.strategy}): '{job.title}' -> '{proposal.new_jd.title}'. "
                      f"{proposal.diagnosis}", actor="replanner", job_id=job_id)
        log_event(db, "Replan proposal waiting for HR approval", actor="replanner", kind="action_needed", job_id=job_id)
    notify_hr(f"Replan proposed for job #{job_id}: {proposal.new_jd.title}", f"/hr/jobs/{job_id}")
    return {"decision": "", "feedback": None}


def review_replan(state: JobState) -> JobState:
    answer = interrupt({"type": "replan_approval", "job_id": state["job_id"]})
    return {"decision": answer.get("action", "approve"), "feedback": answer.get("feedback")}


def apply_replan(state: JobState) -> JobState:
    job_id = state["job_id"]
    with session_scope() as db:
        job = db.get(Job, job_id)
        p = job.replan_proposal
        old_title = job.title
        job.jd = p["new_jd"]
        job.jd_markdown = skills.jd_to_markdown(p["new_jd"])
        job.jd_version += 1
        job.title = p["new_jd"]["title"]
        job.replan_count += 1
        job.application_window_hours = max(0.05, min(240.0, float(p.get("extend_hours") or 48)))
        log_event(db, f"Applied replan #{job.replan_count}: '{old_title}' -> '{job.title}'. Existing applicants "
                      "will be re-scored against the new JD", actor="replanner", job_id=job_id)
    return {"decision": "", "feedback": None}


def screen(state: JobState) -> JobState:
    job_id = state["job_id"]
    _set_status(job_id, JobStatus.SCREENING, "Ranking candidates and building the shortlist")
    _ensure_scored(job_id)
    shortlisted, rejected_contacts = [], []
    with session_scope() as db:
        job = db.get(Job, job_id)
        pool = sorted([a for a in job.applications if a.stage in (Stage.APPLIED, Stage.SCREENED)],
                      key=lambda a: -(a.score or 0))
        for rank, app in enumerate(pool, 1):
            app.rank = rank
            if (app.score or 0) < settings.SCREEN_PASS_SCORE:
                app.stage = Stage.REJECTED
                rejected_contacts.append((app.email, app.phone, app.name))
            elif len(shortlisted) < job.shortlist_size:
                app.stage = Stage.SHORTLISTED
                shortlisted.append(app.id)
            else:
                app.stage = Stage.WAITLISTED
        title = job.title
        if shortlisted:
            job.status = JobStatus.IN_PIPELINE
            log_event(db, f"Shortlisted {len(shortlisted)} candidate(s); "
                          f"{sum(1 for a in pool if a.stage == Stage.WAITLISTED)} waitlisted; "
                          f"{len(rejected_contacts)} not progressed", actor="screening-agent", job_id=job_id)
        else:
            job.status = JobStatus.NEEDS_ATTENTION
            log_event(db, "No qualified candidates after screening. Use 'Replan & repost' or adjust the role.",
                      actor="screening-agent", kind="action_needed", job_id=job_id)
    for email, phone, name in rejected_contacts:
        notify_candidate(email, phone, f"Your application for {title}",
                         f"<p>Hi {name},</p><p>Thank you for applying for <b>{title}</b>. After careful review we "
                         "won't be moving forward at this time. We'll keep your profile for future roles.</p>")
    if shortlisted:
        from .runtime import start_candidate
        for app_id in shortlisted:
            start_candidate(app_id)
        notify_hr(f"{len(shortlisted)} candidates shortlisted for {title} - assessments sent", f"/hr/jobs/{job_id}")
    else:
        notify_hr(f"No qualified candidates for {title}", f"/hr/jobs/{job_id}")
    return {"decision": ""}


def cancel(state: JobState) -> JobState:
    _set_status(state["job_id"], JobStatus.CANCELLED, "Job cancelled")
    return {}


# ------------------------------------------------------------------ routing
def _entry(state: JobState) -> str:
    return "replan" if state.get("entry") == "replan" else "draft_jd"


def _after_review(state: JobState) -> str:
    return {"approve": "publish", "changes": "draft_jd"}.get(state.get("decision"), "cancel")


def _after_wait(state: JobState) -> str:
    return "cancel" if state.get("decision") == "cancel" else "evaluate"


def _after_evaluate(state: JobState) -> str:
    return {"replan": "replan", "cancel": "cancel"}.get(state.get("decision"), "screen")


def _after_replan_review(state: JobState) -> str:
    return {"approve": "apply_replan", "changes": "replan", "cancel": "cancel"}.get(state.get("decision"), "screen")


def build_job_graph(checkpointer):
    g = StateGraph(JobState)
    for name, fn in [("draft_jd", draft_jd), ("review_jd", review_jd), ("publish", publish),
                     ("wait_deadline", wait_deadline), ("evaluate", evaluate), ("replan", replan),
                     ("review_replan", review_replan), ("apply_replan", apply_replan),
                     ("screen", screen), ("cancel", cancel)]:
        g.add_node(name, fn)
    g.add_conditional_edges(START, _entry, ["draft_jd", "replan"])
    g.add_edge("draft_jd", "review_jd")
    g.add_conditional_edges("review_jd", _after_review, ["publish", "draft_jd", "cancel"])
    g.add_edge("publish", "wait_deadline")
    g.add_conditional_edges("wait_deadline", _after_wait, ["evaluate", "cancel"])
    g.add_conditional_edges("evaluate", _after_evaluate, ["replan", "screen", "cancel"])
    g.add_edge("replan", "review_replan")
    g.add_conditional_edges("review_replan", _after_replan_review, ["apply_replan", "replan", "screen", "cancel"])
    g.add_edge("apply_replan", "publish")
    g.add_edge("screen", END)
    g.add_edge("cancel", END)
    return g.compile(checkpointer=checkpointer)

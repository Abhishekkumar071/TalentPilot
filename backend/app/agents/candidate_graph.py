"""Candidate-level agent workflow (one LangGraph thread per shortlisted candidate).

  START ─► invite_assessment ─► wait_assessment ⏸ ─► grade_assessment ─┬─ pass ─► invite_interview ─► wait_interview ⏸
                                                                        └─ fail ─► reject
  ─► grade_interview ─┬─ ok ─► final_report ─► hr_decision ⏸ ─┬─ approve ─► draft_offer ─► offer_review ⏸ ─┬─ approve ─► send_offer
                      └─ fail ─► reject                         └─ reject ─► reject      ▲ redraft ──────────┘ cancel ─► reject
  ─► wait_response ⏸ ─┬─ accepted ─► hired ─► END
                      └─ declined/expired ─► offer_declined ─► (promote next waitlisted candidate) ─► END
Human-in-the-loop: HR must approve the candidate AND the offer before anything is sent.
"""
import logging
from datetime import timedelta
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from sqlalchemy import func, select

from ..config import settings
from ..db import session_scope
from ..documents import offer_letter_pdf
from ..models import Application, Job, JobStatus, Stage
from ..notify import notify_candidate, notify_hr
from ..utils import log_event, now
from . import skills

log = logging.getLogger(__name__)


class CandState(TypedDict, total=False):
    app_id: int
    decision: str
    feedback: str | None


def _link(path: str) -> str:
    return f"{settings.PUBLIC_URL}{path}"


# ------------------------------------------------------------------ nodes
def invite_assessment(state: CandState) -> CandState:
    with session_scope() as db:
        app = db.get(Application, state["app_id"])
        job = app.job
        a = job.assessment or {}
        if not a.get("mcqs") and not a.get("coding"):
            log_event(db, "No assessment configured - skipping to interview", actor="assessment-agent",
                      job_id=job.id, application_id=app.id)
            return {"decision": "skip"}
        app.stage = Stage.ASSESSMENT_SENT
        app.stage_deadline = now() + timedelta(hours=settings.ASSESSMENT_VALID_HOURS)
        name, email, phone, token, title = app.name, app.email, app.phone, app.token, job.title
        log_event(db, f"Assessment invite sent to {name}", actor="assessment-agent", job_id=job.id,
                  application_id=app.id)
    url = _link(f"/assess/{token}")
    notify_candidate(email, phone, f"Next step: online assessment for {title}",
                     f"<p>Hi {name},</p><p>Great news - you've been shortlisted for <b>{title}</b>!</p>"
                     f"<p>Please complete the online assessment within {settings.ASSESSMENT_VALID_HOURS} hours. "
                     "It is AI-proctored: keep your camera on, stay in full-screen and work on your own.</p>",
                     cta=("Start assessment", url),
                     sms_text=f"{settings.COMPANY_NAME}: You're shortlisted for {title}. Take your assessment: {url}")
    return {"decision": ""}


def wait_assessment(state: CandState) -> CandState:
    answer = interrupt({"type": "assessment", "app_id": state["app_id"]})
    return {"decision": answer.get("action", "submitted")}


def grade_assessment(state: CandState) -> CandState:
    app_id = state["app_id"]
    if state.get("decision") == "expired":
        with session_scope() as db:
            log_event(db, "Assessment window expired", actor="assessment-agent", application_id=app_id,
                      job_id=db.get(Application, app_id).job_id)
        return {"decision": "fail", "feedback": "Assessment not completed in time"}
    with session_scope() as db:
        app = db.get(Application, app_id)
        result = skills.grade_assessment(app.job, app.assessment_submission or {})
        result["integrity"] = skills.integrity(db, app_id, "assessment")
        app.assessment_result = result
        app.assessment_score = result["overall"]
        app.stage = Stage.ASSESSMENT_DONE
        passed = result["overall"] >= settings.ASSESSMENT_PASS_SCORE
        log_event(db, f"Assessment graded: {result['overall']}% (MCQ {result['mcq_pct']}%"
                      f"{', coding ' + str(result['coding_pct']) + '%' if result['coding_pct'] is not None else ''}), "
                      f"integrity {result['integrity']['score']} ({result['integrity']['level']}) -> "
                      f"{'PASS' if passed else 'FAIL'}", actor="assessment-agent", job_id=app.job_id,
                  application_id=app_id)
    return {"decision": "pass" if passed else "fail", "feedback": None if passed else "Assessment score below bar"}


def invite_interview(state: CandState) -> CandState:
    with session_scope() as db:
        app = db.get(Application, state["app_id"])
        app.stage = Stage.INTERVIEW_SENT
        app.stage_deadline = now() + timedelta(hours=settings.INTERVIEW_VALID_HOURS)
        name, email, phone, token, title = app.name, app.email, app.phone, app.token, app.job.title
        log_event(db, f"AI interview invite sent to {name}", actor="interview-agent", job_id=app.job_id,
                  application_id=app.id)
    url = _link(f"/interview/{token}")
    notify_candidate(email, phone, f"Interview invitation: {title}",
                     f"<p>Hi {name},</p><p>You cleared the assessment for <b>{title}</b>. The next round is a "
                     f"~20 minute AI-led voice interview you can take any time in the next "
                     f"{settings.INTERVIEW_VALID_HOURS} hours (Chrome recommended, camera + mic required).</p>",
                     cta=("Start interview", url),
                     sms_text=f"{settings.COMPANY_NAME}: Interview invite for {title}: {url}")
    return {"decision": ""}


def wait_interview(state: CandState) -> CandState:
    answer = interrupt({"type": "interview", "app_id": state["app_id"]})
    return {"decision": answer.get("action", "submitted")}


def grade_interview(state: CandState) -> CandState:
    app_id = state["app_id"]
    if state.get("decision") == "expired":
        return {"decision": "fail", "feedback": "Interview not completed in time"}
    with session_scope() as db:
        app = db.get(Application, app_id)
        ev = skills.evaluate_interview(app.job, app.interview_transcript or [])
        data = ev.model_dump()
        data["integrity"] = skills.integrity(db, app_id, "interview")
        app.interview_eval = data
        app.interview_score = float(ev.overall)
        app.stage = Stage.INTERVIEW_DONE
        ok = ev.overall >= 35
        log_event(db, f"Interview evaluated: {ev.overall}/100, recommendation {ev.recommendation}",
                  actor="interview-agent", job_id=app.job_id, application_id=app_id)
    return {"decision": "ok" if ok else "fail", "feedback": None if ok else "Interview score below bar"}


def final_report(state: CandState) -> CandState:
    app_id = state["app_id"]
    with session_scope() as db:
        app = db.get(Application, app_id)
        report = skills.final_report(db, app.job, app)
        app.final_report = report
        app.stage = Stage.HR_REVIEW
        name, title, job_id = app.name, app.job.title, app.job_id
        log_event(db, f"Final report: {report['recommendation']} - {report['headline']}", actor="report-agent",
                  job_id=job_id, application_id=app_id)
        log_event(db, f"HR decision needed for {name}", actor="report-agent", kind="action_needed",
                  job_id=job_id, application_id=app_id)
    notify_hr(f"Candidate ready for your decision: {name} ({title}) - AI says {report['recommendation']}",
              f"/hr/candidates/{app_id}")
    return {"decision": ""}


def hr_decision(state: CandState) -> CandState:
    answer = interrupt({"type": "hr_decision", "app_id": state["app_id"]})
    return {"decision": answer.get("action", "reject"), "feedback": answer.get("feedback")}


def draft_offer(state: CandState) -> CandState:
    app_id = state["app_id"]
    with session_scope() as db:
        app = db.get(Application, app_id)
        offer = skills.draft_offer(db, app.job, app, state.get("feedback"))
        offer["valid_until"] = (now() + timedelta(hours=settings.OFFER_VALID_HOURS)).strftime("%d %b %Y")
        app.offer = offer
        app.stage = Stage.OFFER_REVIEW
        log_event(db, f"Offer drafted: {offer['currency']} {offer['annual_ctc']:,.0f}, joining {offer['joining_date']}",
                  actor="offer-agent", job_id=app.job_id, application_id=app_id)
        log_event(db, f"Offer for {app.name} waiting for HR approval", actor="offer-agent", kind="action_needed",
                  job_id=app.job_id, application_id=app_id)
        name = app.name
    notify_hr(f"Offer letter ready for review: {name}", f"/hr/candidates/{app_id}")
    return {"decision": "", "feedback": None}


def offer_review(state: CandState) -> CandState:
    answer = interrupt({"type": "offer_approval", "app_id": state["app_id"]})
    return {"decision": answer.get("action", "approve"), "feedback": answer.get("feedback")}


def send_offer(state: CandState) -> CandState:
    app_id = state["app_id"]
    with session_scope() as db:
        app = db.get(Application, app_id)
        app.offer = {**app.offer, "valid_until": (now() + timedelta(hours=settings.OFFER_VALID_HOURS)).strftime("%d %b %Y")}
        app.offer_pdf = offer_letter_pdf(app.offer, app.name)
        app.stage = Stage.OFFER_SENT
        app.stage_deadline = now() + timedelta(hours=settings.OFFER_VALID_HOURS)
        name, email, phone, token, title = app.name, app.email, app.phone, app.token, app.job.title
        log_event(db, f"Offer letter sent to {name}", actor="offer-agent", job_id=app.job_id, application_id=app_id)
    url = _link(f"/offer/{token}")
    notify_candidate(email, phone, f"🎉 Offer from {settings.COMPANY_NAME}: {title}",
                     f"<p>Hi {name},</p><p>Congratulations! We're delighted to offer you the role of <b>{title}</b>."
                     " Please review your offer letter and accept or decline using the button below.</p>",
                     cta=("View my offer", url),
                     sms_text=f"{settings.COMPANY_NAME}: Congratulations! Your offer for {title} is ready: {url}")
    return {"decision": ""}


def wait_response(state: CandState) -> CandState:
    answer = interrupt({"type": "candidate_response", "app_id": state["app_id"]})
    return {"decision": answer.get("action", "declined")}


def hired(state: CandState) -> CandState:
    app_id = state["app_id"]
    with session_scope() as db:
        app = db.get(Application, app_id)
        app.stage = Stage.HIRED
        app.stage_deadline = None
        job = app.job
        name, email, phone, title, job_id = app.name, app.email, app.phone, job.title, job.id
        log_event(db, f"🎉 {name} accepted the offer", actor="offer-agent", job_id=job_id, application_id=app_id)
        hires = db.scalar(select(func.count()).select_from(Application).where(
            Application.job_id == job_id, Application.stage == Stage.HIRED))
        filled = hires >= job.openings
        waitlisted = []
        if filled:
            job.status = JobStatus.FILLED
            # Close the waitlist and anyone still in early rounds. HR-review / offer stages are left to HR.
            early = {Stage.WAITLISTED, Stage.SHORTLISTED, Stage.ASSESSMENT_SENT, Stage.ASSESSMENT_DONE,
                     Stage.INTERVIEW_SENT, Stage.INTERVIEW_DONE}
            for a in job.applications:
                if a.stage in early:
                    a.stage = Stage.REJECTED
                    a.stage_deadline = None
                    waitlisted.append((a.email, a.phone, a.name))
            log_event(db, f"Position filled ({hires}/{job.openings}). Closed {len(waitlisted)} other candidate(s) "
                          "still in early rounds.", actor="job-agent", job_id=job_id)
    notify_candidate(email, phone, f"Welcome to {settings.COMPANY_NAME}!",
                     f"<p>Hi {name},</p><p>Thank you for accepting the <b>{title}</b> offer. Our onboarding team "
                     "will contact you with joining formalities and document checklist soon.</p>")
    for e, p, n in waitlisted:
        notify_candidate(e, p, f"Update on {title}",
                         f"<p>Hi {n},</p><p>Thank you for your patience. The <b>{title}</b> position has now been "
                         "filled. We were impressed by your profile and will reach out for future openings.</p>")
    notify_hr(f"{name} accepted the offer for {title}" + (" - position filled ✅" if filled else ""),
              f"/hr/candidates/{app_id}")
    return {}


def offer_declined(state: CandState) -> CandState:
    app_id = state["app_id"]
    with session_scope() as db:
        app = db.get(Application, app_id)
        app.stage = Stage.OFFER_DECLINED
        app.stage_deadline = None
        reason = (app.candidate_response or {}).get("reason", "no reason given")
        log_event(db, f"{app.name} declined the offer ({reason}) - promoting next candidate",
                  actor="offer-agent", job_id=app.job_id, application_id=app_id)
        job_id, name = app.job_id, app.name
    notify_hr(f"{name} declined the offer. Moving to next candidate.", f"/hr/jobs/{job_id}")
    promote_next(job_id)
    return {}


def reject(state: CandState) -> CandState:
    app_id = state["app_id"]
    with session_scope() as db:
        app = db.get(Application, app_id)
        app.stage = Stage.REJECTED
        app.stage_deadline = None
        reason = state.get("feedback") or "not progressed"
        log_event(db, f"{app.name} not progressed: {reason}", actor="pipeline-agent", job_id=app.job_id,
                  application_id=app_id)
        name, email, phone, title, job_id = app.name, app.email, app.phone, app.job.title, app.job_id
    notify_candidate(email, phone, f"Your application for {title}",
                     f"<p>Hi {name},</p><p>Thank you for the time you invested in our process for <b>{title}</b>. "
                     "We've decided not to move forward right now, but we'd love to stay in touch for future roles.</p>")
    promote_next(job_id)
    return {}


# ------------------------------------------------------------------ pipeline helpers
def promote_next(job_id: int) -> None:
    """Keep the pipeline full: when someone drops out, bring in the best waitlisted candidate."""
    start_id = None
    with session_scope() as db:
        job = db.get(Job, job_id)
        if job.status != JobStatus.IN_PIPELINE:
            return
        active = sum(1 for a in job.applications if a.stage in Stage.ACTIVE)
        hired_n = sum(1 for a in job.applications if a.stage == Stage.HIRED)
        if active >= job.shortlist_size:
            return
        waiting = sorted([a for a in job.applications if a.stage == Stage.WAITLISTED], key=lambda a: -(a.score or 0))
        if waiting:
            nxt = waiting[0]
            nxt.stage = Stage.SHORTLISTED
            start_id = nxt.id
            log_event(db, f"Promoted waitlisted candidate {nxt.name} (score {nxt.score}) into the pipeline",
                      actor="pipeline-agent", job_id=job_id, application_id=nxt.id)
        elif active == 0 and hired_n < job.openings:
            job.status = JobStatus.NEEDS_ATTENTION
            log_event(db, "Candidate pool exhausted. Use 'Replan & repost' to attract new applicants.",
                      actor="pipeline-agent", kind="action_needed", job_id=job_id)
            notify_hr(f"Candidate pool exhausted for {job.title} - consider replanning", f"/hr/jobs/{job_id}")
    if start_id:
        from .runtime import start_candidate
        start_candidate(start_id)


# ------------------------------------------------------------------ routing
def build_candidate_graph(checkpointer):
    g = StateGraph(CandState)
    nodes = [invite_assessment, wait_assessment, grade_assessment, invite_interview, wait_interview,
             grade_interview, final_report, hr_decision, draft_offer, offer_review, send_offer, wait_response,
             hired, offer_declined, reject]
    for fn in nodes:
        g.add_node(fn.__name__, fn)
    g.add_edge(START, "invite_assessment")
    g.add_conditional_edges("invite_assessment",
                            lambda s: "invite_interview" if s.get("decision") == "skip" else "wait_assessment",
                            ["invite_interview", "wait_assessment"])
    g.add_edge("wait_assessment", "grade_assessment")
    g.add_conditional_edges("grade_assessment", lambda s: "invite_interview" if s["decision"] == "pass" else "reject",
                            ["invite_interview", "reject"])
    g.add_edge("invite_interview", "wait_interview")
    g.add_edge("wait_interview", "grade_interview")
    g.add_conditional_edges("grade_interview", lambda s: "final_report" if s["decision"] == "ok" else "reject",
                            ["final_report", "reject"])
    g.add_edge("final_report", "hr_decision")
    g.add_conditional_edges("hr_decision", lambda s: "draft_offer" if s["decision"] == "approve" else "reject",
                            ["draft_offer", "reject"])
    g.add_edge("draft_offer", "offer_review")
    g.add_conditional_edges("offer_review",
                            lambda s: {"approve": "send_offer", "redraft": "draft_offer"}.get(s["decision"], "reject"),
                            ["send_offer", "draft_offer", "reject"])
    g.add_edge("send_offer", "wait_response")
    g.add_conditional_edges("wait_response", lambda s: "hired" if s["decision"] == "accepted" else "offer_declined",
                            ["hired", "offer_declined"])
    for terminal in ("hired", "offer_declined", "reject"):
        g.add_edge(terminal, END)
    return g.compile(checkpointer=checkpointer)

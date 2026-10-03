"""HR dashboard API: create hiring requests, approve JDs / replans / candidates /
offers, monitor candidates live, manage the RAG knowledge base and learning memory."""
from datetime import timedelta

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import rag, serializers as ser
from ..agents import runtime, skills
from ..agents.schemas import JobDescription
from ..config import settings
from ..db import get_db
from ..documents import ALLOWED_EXT, extract_text, offer_letter_pdf
from ..models import (ActivityEvent, Application, HRFeedback, Job, JobStatus, KnowledgeDoc, ProctorEvent, Stage)
from ..security import check_credentials, create_token, require_hr
from ..utils import as_utc, log_event, now

auth_router = APIRouter(prefix="/api/auth", tags=["auth"])
router = APIRouter(prefix="/api/hr", tags=["hr"], dependencies=[Depends(require_hr)])


class LoginIn(BaseModel):
    email: str
    password: str


@auth_router.post("/login")
def login(body: LoginIn):
    if not check_credentials(body.email, body.password):
        raise HTTPException(401, "Invalid email or password")
    return {"token": create_token(body.email), "email": body.email}


# ------------------------------------------------------------------ helpers
def _job(db: Session, job_id: int) -> Job:
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


def _app(db: Session, app_id: int) -> Application:
    app = db.get(Application, app_id)
    if not app:
        raise HTTPException(404, "Candidate not found")
    return app


def _expect_job(job_id: int, expected: str) -> None:
    current = runtime.job_waiting_for(job_id)
    if current != expected:
        raise HTTPException(409, f"Job is not waiting for '{expected}' (current: {current or 'busy/none'}). "
                                 "Refresh the page.")


def _expect_app(app_id: int, expected: str) -> None:
    current = runtime.candidate_waiting_for(app_id)
    if current != expected:
        raise HTTPException(409, f"Candidate is not waiting for '{expected}' (current: {current or 'busy/none'}). "
                                 "Refresh the page.")


def _feedback(db: Session, kind: str, note: str, job: Job | None = None, app: Application | None = None):
    """Store an HR decision as learning memory for future agent prompts."""
    if note and note.strip():
        db.add(HRFeedback(kind=kind, note=note.strip()[:2000], role=job.title if job else "",
                          job_id=job.id if job else None, application_id=app.id if app else None))


# ------------------------------------------------------------------ overview
@router.get("/overview")
def overview(db: Session = Depends(get_db)):
    jobs = db.scalars(select(Job).order_by(Job.created_at.desc())).all()
    action_items = []
    for j in jobs:
        if j.status in (JobStatus.AWAITING_JD_APPROVAL, JobStatus.AWAITING_REPLAN_APPROVAL, JobStatus.NEEDS_ATTENTION):
            action_items.append({"kind": j.status, "title": j.title, "link": f"/hr/jobs/{j.id}"})
        if j.last_error:
            action_items.append({"kind": "error", "title": f"{j.title}: agent error", "link": f"/hr/jobs/{j.id}"})
    for a in db.scalars(select(Application).where(Application.stage.in_([Stage.HR_REVIEW, Stage.OFFER_REVIEW]))):
        action_items.append({"kind": a.stage, "title": f"{a.name} - {a.job.title}", "link": f"/hr/candidates/{a.id}"})
    for a in db.scalars(select(Application).where(Application.last_error != "")):
        action_items.append({"kind": "error", "title": f"{a.name}: agent error", "link": f"/hr/candidates/{a.id}"})

    stage_counts = dict(db.execute(select(Application.stage, func.count()).group_by(Application.stage)).all())
    recent = db.scalars(select(ActivityEvent).order_by(ActivityEvent.id.desc()).limit(25)).all()
    return {
        "stats": {
            "open_jobs": sum(1 for j in jobs if j.status not in (JobStatus.FILLED, JobStatus.CANCELLED)),
            "applications": sum(stage_counts.values()),
            "in_pipeline": sum(v for k, v in stage_counts.items() if k in Stage.ACTIVE),
            "hired": stage_counts.get(Stage.HIRED, 0),
        },
        "funnel": stage_counts,
        "action_items": action_items,
        "recent_activity": [ser.activity(e) for e in recent],
        "jobs": [ser.job_summary(j) for j in jobs[:8]],
    }


@router.get("/integrations")
def integrations():
    """Which free services are configured - shown on the Settings page."""
    return {
        "llm_groq": bool(settings.GROQ_API_KEY), "llm_gemini": bool(settings.GEMINI_API_KEY),
        "llm_provider": settings.LLM_PROVIDER, "embeddings": rag.embeddings_enabled(),
        "database": "postgres" if settings.is_postgres else "sqlite (local only)",
        "email": settings.EMAIL_PROVIDER, "telegram_hr": bool(settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_HR_CHAT_ID),
        "telegram_channel": bool(settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHANNEL_ID),
        "sms": bool(settings.TWILIO_ACCOUNT_SID), "public_url": settings.PUBLIC_URL,
        "company": settings.COMPANY_NAME,
    }


# ------------------------------------------------------------------ jobs
class JobIn(BaseModel):
    request_text: str = Field(min_length=5, max_length=3000)
    location: str = ""
    work_mode: str = ""
    department: str = ""
    openings: int = Field(1, ge=1, le=50)
    application_window_hours: float = Field(72, gt=0, le=24 * 60)
    min_applications: int = Field(5, ge=1, le=1000)
    shortlist_size: int = Field(3, ge=1, le=50)


@router.post("/jobs")
def create_job(body: JobIn, db: Session = Depends(get_db)):
    job = Job(request_text=body.request_text, title=body.request_text[:80], location=body.location,
              work_mode=body.work_mode, department=body.department, openings=body.openings,
              application_window_hours=body.application_window_hours, min_applications=body.min_applications,
              shortlist_size=body.shortlist_size)
    db.add(job)
    db.flush()
    log_event(db, f"HR request: \"{body.request_text}\"", actor="hr", job_id=job.id)
    db.commit()
    runtime.start_job(job.id)
    return {"id": job.id}


@router.get("/jobs")
def list_jobs(db: Session = Depends(get_db)):
    return [ser.job_summary(j) for j in db.scalars(select(Job).order_by(Job.created_at.desc()))]


@router.get("/jobs/{job_id}")
def get_job(job_id: int, db: Session = Depends(get_db)):
    # Read workflow state BEFORE the row, so data is never older than the step shown.
    thread = _job(db, job_id).graph_thread
    waiting = runtime.waiting_for(runtime.job_graph, thread)
    running = runtime.is_running(thread)
    db.expire_all()
    job = _job(db, job_id)
    apps = sorted(job.applications, key=lambda a: (a.rank is None, a.rank or 0, -(a.score or 0)))
    events = db.scalars(select(ActivityEvent).where(ActivityEvent.job_id == job_id)
                        .order_by(ActivityEvent.id.desc()).limit(150)).all()
    return {
        **ser.job_detail(job),
        "waiting_for": waiting,
        "agent_running": running,
        "can_retry": bool(job.last_error) and runtime.has_failed_step(runtime.job_graph, thread),
        "applications": [ser.app_summary(a) for a in apps],
        "activity": [ser.activity(e) for e in events],
    }


class JDDecision(BaseModel):
    action: str = Field(pattern="^(approve|changes|cancel)$")
    feedback: str = ""
    edited_jd: dict | None = None


@router.post("/jobs/{job_id}/jd-decision")
def jd_decision(job_id: int, body: JDDecision, db: Session = Depends(get_db)):
    job = _job(db, job_id)
    _expect_job(job_id, "jd_approval")
    if body.action == "changes" and not body.feedback.strip():
        raise HTTPException(400, "Tell the agent what to change")
    if body.edited_jd and body.action == "approve":
        try:
            jd = JobDescription.model_validate(body.edited_jd).model_dump()
        except ValidationError as exc:
            raise HTTPException(400, f"Invalid JD: {exc.errors()[0]['msg']}")
        job.jd, job.jd_markdown, job.title = jd, skills.jd_to_markdown(jd), jd["title"]
        _feedback(db, "jd_manual_edit", body.feedback or "HR edited the JD manually before approving.", job)
    if body.action == "changes":
        _feedback(db, "jd_changes", body.feedback, job)
    log_event(db, f"HR {body.action} JD" + (f": {body.feedback}" if body.feedback else ""), actor="hr", job_id=job_id)
    db.commit()
    runtime.resume_job(job_id, {"action": body.action, "feedback": body.feedback})
    return {"ok": True}


class ReplanDecision(BaseModel):
    action: str = Field(pattern="^(approve|changes|reject|cancel)$")
    feedback: str = ""


@router.post("/jobs/{job_id}/replan-decision")
def replan_decision(job_id: int, body: ReplanDecision, db: Session = Depends(get_db)):
    job = _job(db, job_id)
    _expect_job(job_id, "replan_approval")
    if body.action in ("changes", "reject"):
        _feedback(db, f"replan_{body.action}", body.feedback, job)
    log_event(db, f"HR {body.action} replan" + (f": {body.feedback}" if body.feedback else ""), actor="hr",
              job_id=job_id)
    db.commit()
    runtime.resume_job(job_id, {"action": body.action, "feedback": body.feedback})
    return {"ok": True}


@router.post("/jobs/{job_id}/close-applications")
def close_applications(job_id: int, db: Session = Depends(get_db)):
    _job(db, job_id)
    _expect_job(job_id, "deadline")
    log_event(db, "HR closed applications early", actor="hr", job_id=job_id)
    db.commit()
    runtime.resume_job(job_id, {"action": "close_now"})
    return {"ok": True}


class ExtendIn(BaseModel):
    hours: float = Field(gt=0, le=24 * 30)


@router.post("/jobs/{job_id}/extend")
def extend_deadline(job_id: int, body: ExtendIn, db: Session = Depends(get_db)):
    job = _job(db, job_id)
    if job.status != JobStatus.COLLECTING:
        raise HTTPException(409, "Job is not accepting applications")
    job.deadline = max(as_utc(job.deadline) or now(), now()) + timedelta(hours=body.hours)
    log_event(db, f"HR extended deadline by {body.hours}h", actor="hr", job_id=job_id)
    db.commit()
    return {"deadline": ser.iso(job.deadline)}


@router.post("/jobs/{job_id}/replan-now")
def replan_now(job_id: int, db: Session = Depends(get_db)):
    job = _job(db, job_id)
    if job.status != JobStatus.NEEDS_ATTENTION:
        raise HTTPException(409, "Replanning on demand is available when the job needs attention")
    job.replan_count = min(job.replan_count, settings.MAX_REPLANS - 1)
    log_event(db, "HR triggered AI replanning", actor="hr", job_id=job_id)
    db.commit()
    runtime.start_job(job_id, entry="replan")
    return {"ok": True}


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: int, db: Session = Depends(get_db)):
    job = _job(db, job_id)
    waiting = runtime.job_waiting_for(job_id)
    if waiting in ("jd_approval", "deadline", "replan_approval"):
        runtime.resume_job(job_id, {"action": "cancel"})
    else:
        job.status = JobStatus.CANCELLED
    log_event(db, "HR cancelled the job", actor="hr", job_id=job_id)
    db.commit()
    return {"ok": True}


@router.post("/jobs/{job_id}/retry")
def retry_job(job_id: int, db: Session = Depends(get_db)):
    job = _job(db, job_id)
    if not runtime.has_failed_step(runtime.job_graph, job.graph_thread):
        raise HTTPException(409, "Nothing to retry")
    runtime.retry_job(job_id)
    return {"ok": True}


@router.delete("/jobs/{job_id}")
def delete_job(job_id: int, db: Session = Depends(get_db)):
    db.delete(_job(db, job_id))
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ candidates
@router.get("/candidates/{app_id}")
def get_candidate(app_id: int, db: Session = Depends(get_db)):
    # Read workflow state BEFORE the row, so data is never older than the step shown.
    thread = runtime.app_thread(app_id)
    waiting = runtime.waiting_for(runtime.candidate_graph, thread)
    running = runtime.is_running(thread)
    app = _app(db, app_id)
    events = db.scalars(select(ActivityEvent).where(ActivityEvent.application_id == app_id)
                        .order_by(ActivityEvent.id.desc()).limit(100)).all()
    return {
        **ser.app_detail(app),
        "integrity": skills.integrity(db, app_id),
        "waiting_for": waiting,
        "agent_running": running,
        "can_retry": bool(app.last_error) and runtime.has_failed_step(runtime.candidate_graph, thread),
        "activity": [ser.activity(e) for e in events],
    }


@router.get("/candidates/{app_id}/proctor")
def proctor_events(app_id: int, since_id: int = 0, db: Session = Depends(get_db)):
    _app(db, app_id)
    rows = db.scalars(select(ProctorEvent).where(ProctorEvent.application_id == app_id, ProctorEvent.id > since_id)
                      .order_by(ProctorEvent.id).limit(300)).all()
    return {"events": [ser.proctor(e) for e in rows], "integrity": skills.integrity(db, app_id)}


@router.get("/candidates/{app_id}/resume")
def download_resume(app_id: int, db: Session = Depends(get_db)):
    app = _app(db, app_id)
    if not app.resume_bytes:
        raise HTTPException(404, "No resume file")
    media = "application/pdf" if app.resume_filename.lower().endswith(".pdf") else "application/octet-stream"
    return Response(app.resume_bytes, media_type=media,
                    headers={"Content-Disposition": f'inline; filename="{app.resume_filename}"'})


class CandidateDecision(BaseModel):
    action: str = Field(pattern="^(approve|reject)$")
    feedback: str = ""


@router.post("/candidates/{app_id}/decision")
def candidate_decision(app_id: int, body: CandidateDecision, db: Session = Depends(get_db)):
    app = _app(db, app_id)
    _expect_app(app_id, "hr_decision")
    ai_rec = (app.final_report or {}).get("recommendation", "")
    overrode = (body.action == "approve") != (ai_rec in ("strong_hire", "hire"))
    note = body.feedback.strip()
    if overrode or body.action == "reject":
        _feedback(db, "candidate_" + ("override" if overrode else "rejected"),
                  f"HR {body.action}d candidate (AI said {ai_rec}). {note}".strip(), app.job, app)
    log_event(db, f"HR {body.action}d {app.name}" + (f": {note}" if note else ""), actor="hr",
              job_id=app.job_id, application_id=app_id)
    db.commit()
    runtime.resume_candidate(app_id, {"action": body.action, "feedback": note or "Not selected by hiring team"})
    return {"ok": True}


class OfferDecision(BaseModel):
    action: str = Field(pattern="^(approve|redraft|cancel)$")
    feedback: str = ""
    edits: dict | None = None


@router.post("/candidates/{app_id}/offer-decision")
def offer_decision(app_id: int, body: OfferDecision, db: Session = Depends(get_db)):
    app = _app(db, app_id)
    _expect_app(app_id, "offer_approval")
    if body.action == "approve" and body.edits:
        allowed = {"annual_ctc", "joining_date", "probation", "letter_body", "benefits", "position", "location"}
        edits = {k: v for k, v in body.edits.items() if k in allowed}
        if "annual_ctc" in edits:
            edits["annual_ctc"] = float(edits["annual_ctc"])
        changed = {k: v for k, v in edits.items() if (app.offer or {}).get(k) != v}
        if changed:
            app.offer = {**(app.offer or {}), **changed}
            _feedback(db, "offer_edited", f"HR changed offer fields {sorted(changed)} for {app.job.title}. "
                                          f"{body.feedback}".strip(), app.job, app)
    if body.action == "redraft":
        if not body.feedback.strip():
            raise HTTPException(400, "Tell the agent what to change in the offer")
        _feedback(db, "offer_redraft", body.feedback, app.job, app)
    log_event(db, f"HR {body.action} offer" + (f": {body.feedback}" if body.feedback else ""), actor="hr",
              job_id=app.job_id, application_id=app_id)
    db.commit()
    runtime.resume_candidate(app_id, {"action": body.action, "feedback": body.feedback or "Offer withdrawn by HR"})
    return {"ok": True}


@router.get("/candidates/{app_id}/offer.pdf")
def offer_preview(app_id: int, db: Session = Depends(get_db)):
    app = _app(db, app_id)
    if not app.offer:
        raise HTTPException(404, "No offer drafted")
    pdf = app.offer_pdf or offer_letter_pdf(app.offer, app.name)
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="offer-{app.id}.pdf"'})


@router.post("/candidates/{app_id}/retry")
def retry_candidate(app_id: int, db: Session = Depends(get_db)):
    _app(db, app_id)
    if not runtime.has_failed_step(runtime.candidate_graph, runtime.app_thread(app_id)):
        raise HTTPException(409, "Nothing to retry")
    runtime.retry_candidate(app_id)
    return {"ok": True}


# ------------------------------------------------------------------ knowledge base (RAG)
@router.get("/knowledge")
def list_knowledge(db: Session = Depends(get_db)):
    docs = db.scalars(select(KnowledgeDoc).order_by(KnowledgeDoc.category, KnowledgeDoc.title)).all()
    return [{"id": d.id, "title": d.title, "category": d.category, "chars": len(d.content),
             "chunks": len(d.chunks), "embedded": sum(1 for c in d.chunks if c.embedding),
             "preview": d.content[:300], "created_at": ser.iso(d.created_at)} for d in docs]


@router.get("/knowledge/{doc_id}")
def get_knowledge(doc_id: int, db: Session = Depends(get_db)):
    d = db.get(KnowledgeDoc, doc_id)
    if not d:
        raise HTTPException(404, "Document not found")
    return {"id": d.id, "title": d.title, "category": d.category, "content": d.content}


@router.post("/knowledge")
async def add_knowledge(title: str = Form(...), category: str = Form("general"), content: str = Form(""),
                        file: UploadFile | None = File(None), db: Session = Depends(get_db)):
    if file is not None and file.filename:
        if not any(file.filename.lower().endswith(e) for e in ALLOWED_EXT):
            raise HTTPException(400, "Upload a PDF, DOCX or TXT file")
        content = extract_text(file.filename, await file.read())
    if len(content.strip()) < 20:
        raise HTTPException(400, "Document has no readable text")
    doc = rag.index_document(db, title.strip(), content, category.strip() or "general")
    db.commit()
    return {"id": doc.id, "chunks": len(doc.chunks)}


@router.delete("/knowledge/{doc_id}")
def delete_knowledge(doc_id: int, db: Session = Depends(get_db)):
    d = db.get(KnowledgeDoc, doc_id)
    if d:
        db.delete(d)
        db.commit()
    return {"ok": True}


@router.get("/knowledge-search")
def search_knowledge(q: str, db: Session = Depends(get_db)):
    return rag.search(db, q, k=5)


@router.post("/knowledge/reindex")
def reindex(db: Session = Depends(get_db)):
    n = rag.reindex_missing_embeddings(db)
    db.commit()
    return {"embedded": n}


# ------------------------------------------------------------------ learning memory
@router.get("/feedback")
def list_feedback(db: Session = Depends(get_db)):
    rows = db.scalars(select(HRFeedback).order_by(HRFeedback.created_at.desc()).limit(200)).all()
    return [{"id": r.id, "kind": r.kind, "role": r.role, "note": r.note, "job_id": r.job_id,
             "application_id": r.application_id, "created_at": ser.iso(r.created_at)} for r in rows]


class FeedbackIn(BaseModel):
    note: str = Field(min_length=3, max_length=2000)
    role: str = ""


@router.post("/feedback")
def add_feedback(body: FeedbackIn, db: Session = Depends(get_db)):
    db.add(HRFeedback(kind="hr_guideline", note=body.note.strip(), role=body.role.strip()))
    db.commit()
    return {"ok": True}


@router.delete("/feedback/{fid}")
def delete_feedback(fid: int, db: Session = Depends(get_db)):
    row = db.get(HRFeedback, fid)
    if row:
        db.delete(row)
        db.commit()
    return {"ok": True}

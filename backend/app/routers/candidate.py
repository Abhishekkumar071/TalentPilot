"""Candidate portal, authenticated by the secret token in their email link:
proctored assessment, AI interview, offer accept/decline."""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import serializers as ser
from ..agents import runtime, skills
from ..config import settings
from ..db import get_db
from ..models import Application, ProctorEvent, Stage
from ..utils import as_utc, log_event, now

router = APIRouter(prefix="/api/candidate", tags=["candidate"])

PROCTOR_TYPES = {"tab_hidden", "window_blur", "fullscreen_exit", "paste", "copy", "no_face", "multiple_faces",
                 "looking_away", "phone_detected", "voice_detected", "camera_off", "devtools", "snapshot",
                 "session_start", "right_click", "large_paste"}


def _by_token(db: Session, token: str) -> Application:
    app = db.scalar(select(Application).where(Application.token == token))
    if not app:
        raise HTTPException(404, "Invalid or expired link")
    return app


@router.get("/{token}")
def status(token: str, db: Session = Depends(get_db)):
    app = _by_token(db, token)
    return {"name": app.name, "job_title": app.job.title, "stage": app.stage, "company": settings.COMPANY_NAME,
            "stage_deadline": ser.iso(app.stage_deadline)}


# ------------------------------------------------------------------ assessment
@router.get("/{token}/assessment")
def get_assessment(token: str, db: Session = Depends(get_db)):
    app = _by_token(db, token)
    if app.stage != Stage.ASSESSMENT_SENT:
        return {"available": False, "stage": app.stage, "name": app.name, "job_title": app.job.title}
    return {"available": True, "name": app.name, "job_title": app.job.title, "company": settings.COMPANY_NAME,
            "started_at": ser.iso(app.assessment_started_at), "server_time": now().isoformat(),
            "assessment": skills.public_assessment(app.job.assessment or {})}


@router.post("/{token}/assessment/start")
def start_assessment(token: str, db: Session = Depends(get_db)):
    app = _by_token(db, token)
    if app.stage != Stage.ASSESSMENT_SENT:
        raise HTTPException(409, "Assessment is not available")
    if not app.assessment_started_at:
        app.assessment_started_at = now()
        log_event(db, f"{app.name} started the assessment", actor="candidate", job_id=app.job_id,
                  application_id=app.id)
        db.commit()
    return {"started_at": ser.iso(app.assessment_started_at), "server_time": now().isoformat()}


class CodingAnswer(BaseModel):
    code: str = Field("", max_length=20000)
    passed: int = 0
    total: int = 0


class SubmissionIn(BaseModel):
    mcq_answers: dict[str, int] = {}
    coding: dict[str, CodingAnswer] = {}
    auto_submitted: bool = False


@router.post("/{token}/assessment/submit")
def submit_assessment(token: str, body: SubmissionIn, db: Session = Depends(get_db)):
    app = _by_token(db, token)
    if app.stage != Stage.ASSESSMENT_SENT or app.assessment_submission:
        raise HTTPException(409, "Assessment already submitted or not available")
    if runtime.candidate_waiting_for(app.id) != "assessment":
        raise HTTPException(409, "Assessment is being processed, please wait a moment and retry")
    started = as_utc(app.assessment_started_at) or now()
    app.assessment_submission = {**body.model_dump(), "submitted_at": now().isoformat(),
                                 "elapsed_minutes": round((now() - started).total_seconds() / 60, 1)}
    log_event(db, f"{app.name} submitted the assessment" + (" (time up)" if body.auto_submitted else ""),
              actor="candidate", job_id=app.job_id, application_id=app.id)
    db.commit()
    runtime.resume_candidate(app.id, {"action": "submitted"})
    return {"ok": True}


# ------------------------------------------------------------------ proctoring
class ProctorIn(BaseModel):
    type: str
    severity: str = "low"
    detail: str = Field("", max_length=500)
    snapshot: str | None = Field(None, max_length=200_000)


class ProctorBatch(BaseModel):
    phase: str = Field(pattern="^(assessment|interview)$")
    events: list[ProctorIn] = Field(max_length=50)


@router.post("/{token}/proctor")
def proctor(token: str, body: ProctorBatch, db: Session = Depends(get_db)):
    app = _by_token(db, token)
    allowed_stage = Stage.ASSESSMENT_SENT if body.phase == "assessment" else Stage.INTERVIEW_SENT
    if app.stage != allowed_stage:
        return {"ok": False}
    for e in body.events:
        if e.type not in PROCTOR_TYPES:
            continue
        severity = e.severity if e.severity in ("info", "low", "medium", "high") else "low"
        db.add(ProctorEvent(application_id=app.id, phase=body.phase, type=e.type, severity=severity,
                            detail=e.detail, snapshot=e.snapshot))
    db.commit()
    return {"ok": True, "integrity": skills.integrity(db, app.id, body.phase)["score"]}


# ------------------------------------------------------------------ AI interview
@router.get("/{token}/interview")
def interview_state(token: str, db: Session = Depends(get_db)):
    app = _by_token(db, token)
    return {"available": app.stage == Stage.INTERVIEW_SENT and not app.interview_finished,
            "stage": app.stage, "name": app.name, "job_title": app.job.title, "company": settings.COMPANY_NAME,
            "transcript": app.interview_transcript or [], "total_questions": settings.INTERVIEW_QUESTIONS}


class AnswerIn(BaseModel):
    answer: str = Field("", max_length=6000)


@router.post("/{token}/interview/next")
def interview_next(token: str, body: AnswerIn, db: Session = Depends(get_db)):
    app = _by_token(db, token)
    if app.stage != Stage.INTERVIEW_SENT or app.interview_finished:
        raise HTTPException(409, "Interview is not available")
    transcript = list(app.interview_transcript or [])
    if transcript and transcript[-1]["role"] == "interviewer":
        if not body.answer.strip():
            raise HTTPException(400, "Please answer the question (speak or type)")
        transcript.append({"role": "candidate", "text": body.answer.strip(), "at": now().isoformat()})

    asked = sum(1 for t in transcript if t["role"] == "interviewer")
    if asked >= settings.INTERVIEW_QUESTIONS:
        closing = (f"Thank you {app.name.split()[0]}, that's the end of our interview. The hiring team will "
                   "review it and get back to you soon. Have a great day!")
        transcript.append({"role": "interviewer", "text": closing, "at": now().isoformat(), "closing": True})
        app.interview_transcript = transcript
        app.interview_finished = True
        log_event(db, f"{app.name} completed the AI interview", actor="candidate", job_id=app.job_id,
                  application_id=app.id)
        db.commit()
        if runtime.candidate_waiting_for(app.id) == "interview":
            runtime.resume_candidate(app.id, {"action": "submitted"})
        return {"done": True, "message": closing, "transcript": transcript}

    if not transcript:
        log_event(db, f"{app.name} started the AI interview", actor="candidate", job_id=app.job_id,
                  application_id=app.id)
    turn = skills.interviewer_turn(app.job, app, transcript)
    transcript.append({"role": "interviewer", "text": turn.question, "topic": turn.topic, "at": now().isoformat()})
    app.interview_transcript = transcript
    db.commit()
    return {"done": False, "message": turn.question, "question_number": asked + 1,
            "total_questions": settings.INTERVIEW_QUESTIONS, "transcript": transcript}


# ------------------------------------------------------------------ offer
@router.get("/{token}/offer")
def get_offer(token: str, db: Session = Depends(get_db)):
    app = _by_token(db, token)
    if app.stage not in (Stage.OFFER_SENT, Stage.HIRED, Stage.OFFER_DECLINED) or not app.offer:
        return {"available": False, "stage": app.stage, "name": app.name}
    offer = {k: v for k, v in app.offer.items() if k != "justification"}
    return {"available": True, "stage": app.stage, "name": app.name, "company": settings.COMPANY_NAME,
            "offer": offer, "response": app.candidate_response}


@router.get("/{token}/offer.pdf")
def offer_pdf(token: str, db: Session = Depends(get_db)):
    app = _by_token(db, token)
    if not app.offer_pdf:
        raise HTTPException(404, "Offer letter not available")
    return Response(app.offer_pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="Offer-{settings.COMPANY_NAME}.pdf"'})


class OfferResponse(BaseModel):
    action: str = Field(pattern="^(accept|decline)$")
    reason: str = Field("", max_length=1000)


@router.post("/{token}/offer/respond")
def respond_offer(token: str, body: OfferResponse, db: Session = Depends(get_db)):
    app = _by_token(db, token)
    if app.stage != Stage.OFFER_SENT or runtime.candidate_waiting_for(app.id) != "candidate_response":
        raise HTTPException(409, "This offer can no longer be changed")
    app.candidate_response = {"action": body.action, "reason": body.reason, "at": now().isoformat()}
    db.commit()
    runtime.resume_candidate(app.id, {"action": "accepted" if body.action == "accept" else "declined"})
    return {"ok": True}

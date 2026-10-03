import secrets
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_token() -> str:
    return secrets.token_urlsafe(24)


class JobStatus:
    DRAFTING = "drafting"
    AWAITING_JD_APPROVAL = "awaiting_jd_approval"
    PUBLISHING = "publishing"
    COLLECTING = "collecting"  # live, accepting applications
    EVALUATING = "evaluating"
    REPLANNING = "replanning"
    AWAITING_REPLAN_APPROVAL = "awaiting_replan_approval"
    SCREENING = "screening"
    IN_PIPELINE = "in_pipeline"  # candidates going through test/interview/offer
    NEEDS_ATTENTION = "needs_attention"
    FILLED = "filled"
    CANCELLED = "cancelled"
    FAILED = "failed"


class Stage:
    APPLIED = "applied"
    SCREENED = "screened"
    SHORTLISTED = "shortlisted"
    WAITLISTED = "waitlisted"
    ASSESSMENT_SENT = "assessment_sent"
    ASSESSMENT_DONE = "assessment_done"
    INTERVIEW_SENT = "interview_sent"
    INTERVIEW_DONE = "interview_done"
    HR_REVIEW = "hr_review"
    OFFER_REVIEW = "offer_review"
    OFFER_SENT = "offer_sent"
    HIRED = "hired"
    OFFER_DECLINED = "offer_declined"
    REJECTED = "rejected"

    ACTIVE = {SHORTLISTED, ASSESSMENT_SENT, ASSESSMENT_DONE, INTERVIEW_SENT, INTERVIEW_DONE,
              HR_REVIEW, OFFER_REVIEW, OFFER_SENT}


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    request_text: Mapped[str] = mapped_column(Text)  # what HR typed: "Hire a backend engineer..."
    title: Mapped[str] = mapped_column(String(200), default="Untitled role")
    department: Mapped[str] = mapped_column(String(120), default="")
    location: Mapped[str] = mapped_column(String(120), default="")
    work_mode: Mapped[str] = mapped_column(String(40), default="")
    employment_type: Mapped[str] = mapped_column(String(40), default="Full-time")
    openings: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(40), default=JobStatus.DRAFTING, index=True)
    jd: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    jd_markdown: Mapped[str] = mapped_column(Text, default="")
    jd_version: Mapped[int] = mapped_column(Integer, default=0)
    jd_sources: Mapped[list | None] = mapped_column(JSON, nullable=True)  # RAG citations
    assessment: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    postings: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    application_window_hours: Mapped[float] = mapped_column(Float, default=72.0)
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    min_applications: Mapped[int] = mapped_column(Integer, default=5)
    shortlist_size: Mapped[int] = mapped_column(Integer, default=3)
    replan_count: Mapped[int] = mapped_column(Integer, default=0)
    replan_proposal: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    graph_thread: Mapped[str] = mapped_column(String(80), default="")
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    applications: Mapped[list["Application"]] = relationship(back_populates="job", cascade="all, delete-orphan")


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True, default=new_token)
    name: Mapped[str] = mapped_column(String(160))
    email: Mapped[str] = mapped_column(String(200))
    phone: Mapped[str] = mapped_column(String(40), default="")
    resume_filename: Mapped[str] = mapped_column(String(255), default="")
    resume_bytes: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    resume_text: Mapped[str] = mapped_column(Text, default="")
    parsed: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    embedding: Mapped[list | None] = mapped_column(JSON, nullable=True)
    screening: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    scored_version: Mapped[int] = mapped_column(Integer, default=-1)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stage: Mapped[str] = mapped_column(String(40), default=Stage.APPLIED, index=True)
    stage_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    assessment_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    assessment_submission: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    assessment_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    assessment_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    interview_transcript: Mapped[list | None] = mapped_column(JSON, nullable=True)
    interview_eval: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    interview_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    interview_finished: Mapped[bool] = mapped_column(Boolean, default=False)

    final_report: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    offer: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    offer_pdf: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    candidate_response: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    job: Mapped[Job] = relationship(back_populates="applications")


class ProctorEvent(Base):
    __tablename__ = "proctor_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), index=True)
    phase: Mapped[str] = mapped_column(String(20))  # assessment | interview
    type: Mapped[str] = mapped_column(String(40))
    severity: Mapped[str] = mapped_column(String(10), default="low")  # info | low | medium | high
    detail: Mapped[str] = mapped_column(Text, default="")
    snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)  # small base64 JPEG
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class KnowledgeDoc(Base):
    __tablename__ = "knowledge_docs"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(60), default="general")
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    chunks: Mapped[list["KnowledgeChunk"]] = relationship(back_populates="doc", cascade="all, delete-orphan")


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"

    id: Mapped[int] = mapped_column(primary_key=True)
    doc_id: Mapped[int] = mapped_column(ForeignKey("knowledge_docs.id", ondelete="CASCADE"), index=True)
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list | None] = mapped_column(JSON, nullable=True)

    doc: Mapped[KnowledgeDoc] = relationship(back_populates="chunks")


class ActivityEvent(Base):
    """Timeline of everything agents, HR and candidates did - shown on the dashboard."""
    __tablename__ = "activity_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True, nullable=True)
    application_id: Mapped[int | None] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), index=True, nullable=True)
    actor: Mapped[str] = mapped_column(String(30))  # agent name | hr | candidate | system
    kind: Mapped[str] = mapped_column(String(40))  # info | action_needed | error | notification
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class HRFeedback(Base):
    """Learning memory: HR decisions are replayed as guidance in future prompts."""
    __tablename__ = "hr_feedback"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True)
    application_id: Mapped[int | None] = mapped_column(
        ForeignKey("applications.id", ondelete="SET NULL"), nullable=True)
    kind: Mapped[str] = mapped_column(String(40))  # jd_changes | replan_rejected | candidate_rejected | ...
    role: Mapped[str] = mapped_column(String(200), default="")
    note: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

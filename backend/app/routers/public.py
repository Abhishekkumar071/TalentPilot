"""Careers site: list open jobs and accept applications (no login)."""
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import serializers as ser
from ..config import settings
from ..db import get_db
from ..documents import ALLOWED_EXT, extract_text
from ..models import Application, Job, JobStatus
from ..scheduler import score_in_background
from ..utils import as_utc, log_event, now

router = APIRouter(prefix="/api/public", tags=["public"])
_email = TypeAdapter(EmailStr)


def _public_job(job: Job) -> dict:
    jd = job.jd or {}
    open_ = job.status == JobStatus.COLLECTING and (job.deadline is None or as_utc(job.deadline) > now())
    return {
        "id": job.id, "title": job.title, "department": job.department, "location": job.location,
        "work_mode": job.work_mode, "employment_type": job.employment_type,
        "experience": f"{jd.get('experience_min', '?')}-{jd.get('experience_max', '?')} yrs",
        "salary": (f"{jd.get('currency', 'INR')} {jd.get('salary_min', 0):,} - {jd.get('salary_max', 0):,}"
                   if jd.get("salary_max") else None),
        "skills": jd.get("must_have_skills", []), "summary": jd.get("summary", ""),
        "jd_markdown": job.jd_markdown, "deadline": ser.iso(job.deadline), "open": open_,
    }


@router.get("/config")
def public_config():
    return {"app_name": settings.APP_NAME, "company": settings.COMPANY_NAME}


@router.get("/jobs")
def open_jobs(db: Session = Depends(get_db)):
    jobs = db.scalars(select(Job).where(Job.status == JobStatus.COLLECTING).order_by(Job.created_at.desc())).all()
    return [_public_job(j) for j in jobs]


@router.get("/jobs/{job_id}")
def job_detail(job_id: int, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job or not job.jd:
        raise HTTPException(404, "Job not found")
    return _public_job(job)


@router.post("/jobs/{job_id}/apply")
async def apply(job_id: int, name: str = Form(..., min_length=2, max_length=160),
                email: str = Form(...), phone: str = Form("", max_length=40),
                consent: bool = Form(...), resume: UploadFile = File(...), db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job or not _public_job(job)["open"]:
        raise HTTPException(400, "This job is not accepting applications")
    if not consent:
        raise HTTPException(400, "Please accept the AI-processing consent to apply")
    try:
        email = _email.validate_python(email.strip()).lower()
    except ValidationError:
        raise HTTPException(400, "Enter a valid email")
    if db.scalar(select(func.count()).select_from(Application).where(
            Application.job_id == job_id, func.lower(Application.email) == email)):
        raise HTTPException(409, "You have already applied for this job")

    filename = resume.filename or "resume"
    if not any(filename.lower().endswith(e) for e in ALLOWED_EXT):
        raise HTTPException(400, "Resume must be a PDF, DOCX or TXT file")
    data = await resume.read()
    if len(data) > settings.MAX_RESUME_MB * 1024 * 1024:
        raise HTTPException(400, f"Resume must be under {settings.MAX_RESUME_MB} MB")
    try:
        text = extract_text(filename, data)
    except Exception:
        raise HTTPException(400, "Could not read that file - please upload a text-based PDF or DOCX")
    if len(text) < 150:
        raise HTTPException(400, "We couldn't read enough text from your resume (scanned image?). "
                                 "Please upload a text-based PDF or DOCX.")

    app = Application(job_id=job_id, name=name.strip(), email=email, phone=phone.strip(),
                      resume_filename=filename[:255], resume_bytes=data, resume_text=text)
    db.add(app)
    db.flush()
    log_event(db, f"New application from {app.name}", actor="candidate", job_id=job_id, application_id=app.id)
    db.commit()
    score_in_background(app.id)
    return {"ok": True, "message": "Application received! Our AI recruiter will review it and email you the next steps."}

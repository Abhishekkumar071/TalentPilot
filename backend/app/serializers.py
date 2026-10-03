"""Convert ORM rows to JSON for the API (never leaks resume bytes or answer keys)."""
from .models import ActivityEvent, Application, Job, ProctorEvent
from .utils import as_utc


def iso(dt):
    dt = as_utc(dt)
    return dt.isoformat() if dt else None


def job_summary(job: Job) -> dict:
    apps = job.applications
    return {
        "id": job.id, "title": job.title, "department": job.department, "location": job.location,
        "work_mode": job.work_mode, "employment_type": job.employment_type, "status": job.status,
        "openings": job.openings, "deadline": iso(job.deadline), "replan_count": job.replan_count,
        "jd_version": job.jd_version, "created_at": iso(job.created_at), "last_error": job.last_error,
        "counts": {
            "applications": len(apps),
            "in_pipeline": sum(1 for a in apps if a.stage not in ("applied", "screened", "rejected", "waitlisted")),
            "hired": sum(1 for a in apps if a.stage == "hired"),
        },
    }


def job_detail(job: Job) -> dict:
    return {
        **job_summary(job),
        "request_text": job.request_text, "jd": job.jd, "jd_markdown": job.jd_markdown,
        "jd_sources": job.jd_sources or [], "postings": job.postings or {},
        "replan_proposal": job.replan_proposal, "min_applications": job.min_applications,
        "shortlist_size": job.shortlist_size, "application_window_hours": job.application_window_hours,
        "assessment": job.assessment,
    }


def app_summary(a: Application) -> dict:
    return {
        "id": a.id, "job_id": a.job_id, "name": a.name, "email": a.email, "phone": a.phone,
        "stage": a.stage, "score": a.score, "rank": a.rank, "assessment_score": a.assessment_score,
        "interview_score": a.interview_score,
        "recommendation": (a.final_report or {}).get("recommendation"),
        "experience": (a.parsed or {}).get("total_experience_years"),
        "created_at": iso(a.created_at), "last_error": a.last_error,
    }


def app_detail(a: Application) -> dict:
    return {
        **app_summary(a),
        "job_title": a.job.title, "resume_filename": a.resume_filename, "resume_text": a.resume_text[:20000],
        "parsed": a.parsed, "screening": a.screening, "stage_deadline": iso(a.stage_deadline),
        "assessment_started_at": iso(a.assessment_started_at), "assessment_result": a.assessment_result,
        "assessment_submission": a.assessment_submission,
        "interview_transcript": a.interview_transcript or [], "interview_eval": a.interview_eval,
        "final_report": a.final_report, "offer": a.offer, "candidate_response": a.candidate_response,
        "has_offer_pdf": a.offer_pdf is not None,
    }


def activity(e: ActivityEvent) -> dict:
    return {"id": e.id, "job_id": e.job_id, "application_id": e.application_id, "actor": e.actor,
            "kind": e.kind, "message": e.message, "data": e.data, "created_at": iso(e.created_at)}


def proctor(e: ProctorEvent, with_snapshot: bool = True) -> dict:
    return {"id": e.id, "phase": e.phase, "type": e.type, "severity": e.severity, "detail": e.detail,
            "snapshot": e.snapshot if with_snapshot else None, "has_snapshot": bool(e.snapshot),
            "created_at": iso(e.created_at)}

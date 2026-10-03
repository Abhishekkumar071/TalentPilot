"""The individual 'skills' agents use. Each one is a focused LLM call with RAG
context + learned HR feedback, returning a validated Pydantic object."""
import json
import logging
import re
import subprocess
import sys
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import llm, rag
from ..config import settings
from ..models import Application, HRFeedback, Job, ProctorEvent
from ..utils import anonymize, bullet
from . import schemas as S

log = logging.getLogger(__name__)

SYSTEM_BASE = (f"You are an expert talent-acquisition agent working for {settings.COMPANY_NAME}. "
               "Be factual, concise and fair. Never invent company facts that contradict the provided context.")


# ------------------------------------------------------------------ learning
def learned_guidance(db: Session, role: str, limit: int = 8) -> str:
    """Past HR feedback, most relevant (same role words) first. This is how the
    system 'learns' from HR without fine-tuning: feedback becomes prompt guidance."""
    rows = db.scalars(select(HRFeedback).order_by(HRFeedback.created_at.desc()).limit(60)).all()
    words = set(re.findall(r"[a-z]+", role.lower()))
    rows = sorted(rows, key=lambda r: -len(words & set(re.findall(r"[a-z]+", r.role.lower()))))[:limit]
    if not rows:
        return "No previous HR feedback yet."
    return bullet(f"[{r.kind}] ({r.role or 'general'}) {r.note}" for r in rows)


# ------------------------------------------------------------------ JD
def jd_to_text(jd: dict) -> str:
    return "\n".join([
        f"{jd.get('title')} | {jd.get('department')} | {jd.get('location')} ({jd.get('work_mode')})",
        f"Experience: {jd.get('experience_min')}-{jd.get('experience_max')} years",
        jd.get("summary", ""),
        "Responsibilities:\n" + bullet(jd.get("responsibilities")),
        "Must have: " + ", ".join(jd.get("must_have_skills", [])),
        "Nice to have: " + ", ".join(jd.get("nice_to_have_skills", [])),
        "Qualifications:\n" + bullet(jd.get("qualifications")),
    ])


def jd_to_markdown(jd: dict) -> str:
    salary = f"{jd.get('currency', 'INR')} {jd.get('salary_min', 0):,} - {jd.get('salary_max', 0):,} per annum"
    sections = [
        f"# {jd['title']}",
        f"**{settings.COMPANY_NAME}** · {jd.get('department')} · {jd.get('location')} · "
        f"{jd.get('work_mode', '').title()} · {jd.get('employment_type')}",
        f"**Experience:** {jd.get('experience_min')}-{jd.get('experience_max')} years  \n**Compensation:** {salary}",
        "## About the role", jd.get("summary", ""),
        "## About us", jd.get("about_company", ""),
        "## What you'll do", bullet(jd.get("responsibilities")),
        "## Must-have skills", bullet(jd.get("must_have_skills")),
        "## Nice to have", bullet(jd.get("nice_to_have_skills")),
        "## Qualifications", bullet(jd.get("qualifications")),
        "## Benefits", bullet(jd.get("benefits")),
        "## Hiring process", "\n".join(f"{i}. {s}" for i, s in enumerate(jd.get("hiring_process", []), 1)),
        f"*{settings.COMPANY_NAME} is an equal opportunity employer.*",
    ]
    return "\n\n".join(s for s in sections if s)


def parse_request(request_text: str, overrides: dict) -> S.HiringRequest:
    user = (f"HR request: \"{request_text}\"\nForm overrides (take precedence if non-empty): {json.dumps(overrides)}\n"
            "Extract the structured hiring request. Use sensible defaults for anything not mentioned.")
    return llm.structured(S.HiringRequest, SYSTEM_BASE, user, tier="fast")


def draft_jd(db: Session, req: S.HiringRequest, hr_feedback: str | None = None,
             previous_jd: dict | None = None) -> tuple[S.JobDescription, list[dict]]:
    context, sources = rag.gather_context(db, [
        f"{req.title} role responsibilities tech stack",
        f"salary band compensation {req.title} {req.experience_min} years",
        "benefits leave insurance perks",
        "hiring policy inclusive language job description rules",
        "company overview mission culture",
        f"{req.department} team",
    ])
    user = f"""Write a compelling, accurate job description.

HIRING REQUEST:
{req.model_dump_json(indent=1)}

COMPANY KNOWLEDGE (retrieved via RAG - salary MUST come from the matching salary band):
{context or 'No documents found - use reasonable market defaults for India.'}

LESSONS FROM PAST HR FEEDBACK (follow these):
{learned_guidance(db, req.title)}
"""
    if previous_jd and hr_feedback:
        user += f"\nPREVIOUS DRAFT:\n{jd_to_text(previous_jd)}\n\nHR REQUESTED THESE CHANGES (apply all of them):\n{hr_feedback}\n"
    jd = llm.structured(S.JobDescription, SYSTEM_BASE + " You write job descriptions that attract strong, diverse "
                        "candidates and strictly follow company policy.", user)
    return jd, sources


def linkedin_post(jd: dict, url: str) -> str:
    system = SYSTEM_BASE + " You write engaging LinkedIn hiring posts."
    user = (f"Write a LinkedIn hiring post (max 1200 chars, 3-5 relevant hashtags, a few emojis) for:\n{jd_to_text(jd)}\n"
            f"End with: Apply here: {url}")
    try:
        return llm.text(system, user, tier="fast")
    except Exception:
        return f"We're hiring a {jd['title']} at {settings.COMPANY_NAME}! Apply here: {url}"


def naukri_text(jd: dict, url: str) -> str:
    return "\n".join([
        f"Job Title: {jd['title']}", f"Company: {settings.COMPANY_NAME}",
        f"Location: {jd.get('location')} ({jd.get('work_mode')})",
        f"Experience: {jd.get('experience_min')}-{jd.get('experience_max')} Years",
        f"Salary: {jd.get('currency')} {jd.get('salary_min'):,} - {jd.get('salary_max'):,} P.A.",
        f"Employment Type: {jd.get('employment_type')}",
        f"Key Skills: {', '.join(jd.get('must_have_skills', []) + jd.get('nice_to_have_skills', []))}",
        "", "Job Description:", jd.get("summary", ""), "", "Responsibilities:", bullet(jd.get("responsibilities")),
        "", "Requirements:", bullet(jd.get("qualifications")), "", f"Apply: {url}",
    ])


# ------------------------------------------------------------------ assessment
_HARNESS = r"""
import json, sys
p = json.loads(sys.stdin.read())
ns = {}
exec(p["code"], ns)
fn = ns[p["fn"]]
out = []
for raw in p["tests"]:
    try:
        args = json.loads(raw)
        res = fn(*args) if isinstance(args, list) else fn(args)
        out.append(json.dumps(res, sort_keys=True))
    except Exception:
        out.append(None)
print(json.dumps(out))
"""


def _canon(raw: str) -> str | None:
    try:
        return json.dumps(json.loads(raw), sort_keys=True)
    except Exception:
        return None


def _validate_problem(p: S.CodingProblem) -> dict | None:
    """Run the LLM's reference solution against its own tests in an isolated
    subprocess; keep only tests that agree, so candidates never get a broken test."""
    tests = [t for t in p.tests if _canon(t.args_json) is not None and _canon(t.expected_json) is not None]
    if not tests or not re.fullmatch(r"[A-Za-z_]\w*", p.function_name):
        return None
    try:
        proc = subprocess.run(
            [sys.executable, "-I", "-c", _HARNESS],
            input=json.dumps({"code": p.reference_solution, "fn": p.function_name,
                              "tests": [t.args_json for t in tests]}),
            capture_output=True, text=True, timeout=8,
        )
        results = json.loads(proc.stdout.strip().splitlines()[-1])
    except Exception as exc:
        log.warning("Could not validate coding problem %r: %s", p.title, exc)
        return None
    good = [t for t, r in zip(tests, results) if r is not None and r == _canon(t.expected_json)]
    if len(good) < 3:
        return None
    data = p.model_dump()
    data["tests"] = [{"args_json": t.args_json, "expected_json": t.expected_json} for t in good]
    return data


def generate_assessment(db: Session, jd: dict) -> dict:
    user = f"""Create an online screening assessment for this role.
- 6 MCQs that test real, practical knowledge of the must-have skills (not trivia), exactly 4 options each.
- For software/technical roles: exactly 1 Python coding problem (easy-medium, solvable in 15 minutes) with a
  correct reference solution and 5 test cases. Arguments and expected values must be plain JSON.
- For non-technical roles: coding must be an empty list.

ROLE:
{jd_to_text(jd)}"""
    for _ in range(2):
        a = llm.structured(S.Assessment, SYSTEM_BASE + " You design fair, job-relevant technical assessments.", user)
        mcqs = [m.model_dump() for m in a.mcqs if len(m.options) >= 2 and 0 <= m.answer_index < len(m.options)]
        coding = [v for v in (_validate_problem(p) for p in a.coding) if v]
        if coding or not a.coding:
            break
        log.info("Generated coding problem failed validation, regenerating")
    return {"duration_minutes": max(15, min(60, a.duration_minutes)), "mcqs": mcqs, "coding": coding}


def public_assessment(assessment: dict) -> dict:
    """What the candidate's browser receives - no answers, no reference solution."""
    return {
        "duration_minutes": assessment.get("duration_minutes", 30),
        "mcqs": [{"question": m["question"], "options": m["options"], "skill": m.get("skill", "")}
                 for m in assessment.get("mcqs", [])],
        "coding": [{k: p[k] for k in ("title", "description", "function_name", "starter_code", "difficulty", "tests")}
                   for p in assessment.get("coding", [])],
    }


# ------------------------------------------------------------------ screening
def parse_resume(app: Application) -> S.ResumeProfile:
    user = f"Extract a structured profile from this resume:\n\n{app.resume_text[:12000]}"
    return llm.structured(S.ResumeProfile, SYSTEM_BASE + " You parse resumes precisely.", user, tier="fast")


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9+#]", "", s.lower())


def skill_present(skill: str, text_lower: str, text_norm: str) -> bool:
    n = _norm(skill)
    if not n:
        return False
    if len(n) <= 3:  # 'go', 'c', 'aws' need word boundaries
        return re.search(rf"(?<![a-z0-9]){re.escape(skill.lower())}(?![a-z0-9])", text_lower) is not None
    return n in text_norm


def _experience_fit(years: float, lo: int, hi: int) -> float:
    if lo <= years <= hi:
        return 100.0
    if years < lo:
        return max(0.0, 100 - 30 * (lo - years))
    return max(60.0, 100 - 8 * (years - hi))


_jd_vec_cache: dict[tuple[int, int], list[float] | None] = {}


def score_application(db: Session, app: Application, job: Job) -> dict:
    """Hybrid score = LLM rubric (45%) + must-have skill match (25%) +
    semantic similarity (15%) + experience fit (15%)."""
    jd = job.jd or {}
    if not app.parsed:
        app.parsed = parse_resume(app).model_dump()
    if app.embedding is None:
        app.embedding = rag.embed_one(app.resume_text[:8000], task="RETRIEVAL_DOCUMENT")

    text_lower = app.resume_text.lower()
    text_norm = _norm(app.resume_text)
    musts = jd.get("must_have_skills", [])

    verdict = llm.structured(S.ScreeningVerdict, SYSTEM_BASE + " You screen resumes objectively. Ignore name, "
                             "gender, age, college prestige and location; judge only skills and evidence.", f"""
JOB:
{jd_to_text(jd)}

CANDIDATE RESUME (anonymized):
{anonymize(app.resume_text, app.name)[:10000]}

HR PREFERENCES LEARNED FROM PAST DECISIONS:
{learned_guidance(db, job.title)}

Score 0-100 for fit. 80+ = strong match, 60-79 = good, 40-59 = partial, <40 = poor.""", tier="fast")

    matched = {m for m in musts if skill_present(m, text_lower, text_norm)}
    matched |= {m for m in musts if m.lower() in {x.lower() for x in verdict.must_have_matched}}
    skill_score = 100 * len(matched) / len(musts) if musts else 60.0

    key = (job.id, job.jd_version)
    if key not in _jd_vec_cache:
        _jd_vec_cache[key] = rag.embed_one(jd_to_text(jd), task="RETRIEVAL_QUERY")
    sim = rag.cosine(app.embedding, _jd_vec_cache[key])
    if sim is not None:
        semantic = max(0.0, min(100.0, (sim - 0.45) / 0.35 * 100))
    else:  # keyword overlap fallback
        vocab = {w for w in re.findall(r"[a-z+#]{3,}", jd_to_text(jd).lower())}
        semantic = 100 * len({w for w in vocab if w in text_lower}) / max(1, len(vocab))

    years = float((app.parsed or {}).get("total_experience_years") or 0)
    exp_fit = _experience_fit(years, int(jd.get("experience_min", 0)), int(jd.get("experience_max", 99)))
    llm_score = max(0, min(100, verdict.score))
    total = round(0.45 * llm_score + 0.25 * skill_score + 0.15 * semantic + 0.15 * exp_fit, 1)
    return {
        "total": total,
        "breakdown": {"llm_rubric": llm_score, "skills": round(skill_score, 1),
                      "semantic": round(semantic, 1), "experience": round(exp_fit, 1)},
        "semantic_method": "embeddings" if sim is not None else "keywords",
        "must_have_matched": sorted(matched),
        "must_have_missing": [m for m in musts if m not in matched],
        "strengths": verdict.strengths, "gaps": verdict.gaps, "reasoning": verdict.reasoning,
        "jd_version": job.jd_version,
    }


# ------------------------------------------------------------------ replanning
def propose_replan(db: Session, job: Job, metrics: dict, sample_gaps: list[str],
                   hr_feedback: str | None = None) -> S.ReplanProposal:
    context, _ = rag.gather_context(db, [f"salary band {job.title}", "related roles career framework",
                                         "remote work policy", "hiring policy"], k_each=2)
    user = f"""Our job posting is under-performing. Diagnose why and rewrite the JD to attract more qualified applicants.
You MAY broaden the role (e.g. 'Backend Engineer' -> 'Full Stack Engineer' or 'Software Engineer (Backend)'),
relax non-essential requirements, adjust experience range, allow remote/hybrid if policy permits, or raise
compensation within the allowed band. Keep it truthful to the business need.

CURRENT JD:
{jd_to_text(job.jd or {})}
Salary: {(job.jd or {}).get('salary_min')} - {(job.jd or {}).get('salary_max')}

PIPELINE METRICS:
{json.dumps(metrics, indent=1)}

COMMON GAPS SEEN IN APPLICANTS:
{bullet(sample_gaps[:12]) or 'n/a'}

COMPANY CONTEXT:
{context}

LESSONS FROM PAST HR FEEDBACK:
{learned_guidance(db, job.title)}
"""
    if hr_feedback:
        user += f"\nHR rejected your previous proposal with this feedback - address it:\n{hr_feedback}\n"
    return llm.structured(S.ReplanProposal, SYSTEM_BASE + " You are a recruitment strategist.", user)


# ------------------------------------------------------------------ grading
def grade_assessment(job: Job, submission: dict) -> dict:
    a = job.assessment or {}
    answers = submission.get("mcq_answers", {})
    mcqs = a.get("mcqs", [])
    correct = sum(1 for i, m in enumerate(mcqs) if str(answers.get(str(i))) == str(m["answer_index"]))
    mcq_pct = 100 * correct / len(mcqs) if mcqs else 0.0

    coding_results = []
    for i, prob in enumerate(a.get("coding", [])):
        sub = (submission.get("coding") or {}).get(str(i), {})
        code = (sub.get("code") or "").strip()
        total = len(prob["tests"])
        passed = max(0, min(total, int(sub.get("passed", 0))))  # client-side Pyodide result
        if not code or code == prob["starter_code"].strip():
            coding_results.append({"title": prob["title"], "score": 0, "passed": 0, "total": total,
                                   "review": {"feedback": "No solution submitted."}})
            continue
        try:
            review = llm.structured(S.CodeReview, SYSTEM_BASE + " You are a senior engineer reviewing interview code.",
                                    f"PROBLEM:\n{prob['description']}\n\nREFERENCE SOLUTION:\n{prob['reference_solution']}"
                                    f"\n\nCANDIDATE CODE:\n{code[:6000]}", tier="fast").model_dump()
        except Exception as exc:
            log.warning("Code review failed: %s", exc)
            review = {"correctness": round(10 * passed / max(1, total)), "quality": 5,
                      "ai_generated_likelihood": "low", "feedback": "Automatic review unavailable."}
        score = 50 * passed / max(1, total) + 3.5 * review["correctness"] + 1.5 * review["quality"]
        coding_results.append({"title": prob["title"], "score": round(score, 1), "passed": passed,
                               "total": total, "review": review})

    if coding_results:
        coding_pct = sum(r["score"] for r in coding_results) / len(coding_results)
        overall = 0.4 * mcq_pct + 0.6 * coding_pct
    else:
        coding_pct, overall = None, mcq_pct
    return {"mcq_correct": correct, "mcq_total": len(mcqs), "mcq_pct": round(mcq_pct, 1),
            "coding": coding_results, "coding_pct": None if coding_pct is None else round(coding_pct, 1),
            "overall": round(overall, 1)}


# ------------------------------------------------------------------ proctoring
SEVERITY_PENALTY = {"info": 0, "low": 2, "medium": 5, "high": 10}
TYPE_CAP = 30  # one noisy signal (e.g. lighting) can't zero the score alone


def integrity(db: Session, application_id: int, phase: str | None = None) -> dict:
    q = select(ProctorEvent).where(ProctorEvent.application_id == application_id)
    if phase:
        q = q.where(ProctorEvent.phase == phase)
    events = db.scalars(q).all()
    per_type: dict[str, float] = {}
    counts: dict[str, int] = {}
    for e in events:
        counts[e.type] = counts.get(e.type, 0) + 1
        per_type[e.type] = min(TYPE_CAP, per_type.get(e.type, 0) + SEVERITY_PENALTY.get(e.severity, 2))
    score = max(0.0, 100 - sum(per_type.values()))
    level = "clean" if score >= 85 else "minor_flags" if score >= 60 else "suspicious"
    return {"score": round(score, 1), "level": level, "counts": counts}


# ------------------------------------------------------------------ interview
def interviewer_turn(job: Job, app: Application, transcript: list[dict]) -> S.InterviewTurn:
    asked = sum(1 for t in transcript if t["role"] == "interviewer")
    total = settings.INTERVIEW_QUESTIONS
    convo = "\n".join(f"{t['role'].upper()}: {t['text']}" for t in transcript[-12:]) or "(interview not started)"
    profile = app.parsed or {}
    result = app.assessment_result or {}
    user = f"""You are conducting a live voice interview for {job.title} at {settings.COMPANY_NAME}.
This is question {asked + 1} of {total}.
Plan: Q1 warm intro about their background -> technical depth on must-have skills and their projects ->
one scenario/system-design style question -> one behavioural question -> last question about motivation.
Adapt: if the previous answer was weak or vague, ask one probing follow-up; if strong, go deeper.
Keep each question short (spoken aloud, max 45 words). Do not reveal scores. Do not repeat questions.

JOB MUST-HAVES: {', '.join((job.jd or {}).get('must_have_skills', []))}
CANDIDATE PROFILE: {profile.get('summary', '')} | skills: {', '.join(profile.get('skills', [])[:15])}
PROJECTS: {'; '.join(profile.get('projects', [])[:4])}
ASSESSMENT: overall {result.get('overall', 'n/a')}%

CONVERSATION SO FAR:
{convo}"""
    return llm.structured(S.InterviewTurn, SYSTEM_BASE + " You are a friendly but rigorous interviewer.", user)


def evaluate_interview(job: Job, transcript: list[dict]) -> S.InterviewEvaluation:
    convo = "\n".join(f"{t['role'].upper()}: {t['text']}" for t in transcript)
    user = f"""Evaluate this interview for {job.title}.
JOB:
{jd_to_text(job.jd or {})}

TRANSCRIPT:
{convo[:14000]}

Score strictly on evidence in the answers. Short, vague or missing answers must score low."""
    return llm.structured(S.InterviewEvaluation, SYSTEM_BASE + " You are a calibrated hiring-panel evaluator.", user)


# ------------------------------------------------------------------ final report & offer
def final_report(db: Session, job: Job, app: Application) -> dict:
    integ = integrity(db, app.id)
    resume = app.score or 0
    assess = app.assessment_score or 0
    interview = app.interview_score or 0
    overall = round(0.2 * resume + 0.3 * assess + 0.5 * interview, 1)
    payload = {
        "resume_screening": app.screening, "assessment": app.assessment_result,
        "integrity": integ, "interview": app.interview_eval, "weighted_overall": overall,
    }
    report = llm.structured(S.FinalReport, SYSTEM_BASE + " You write hiring committee summaries.", f"""
Write the final hiring recommendation for {app.name} for the role {job.title}.
If integrity level is 'suspicious', mention it clearly as a risk and recommend human verification.

EVIDENCE:
{json.dumps(payload, default=str)[:12000]}

HR PREFERENCES LEARNED FROM PAST DECISIONS:
{learned_guidance(db, job.title)}""")
    return {**report.model_dump(), "scores": {"resume": resume, "assessment": assess, "interview": interview,
                                               "integrity": integ["score"], "overall": overall},
            "integrity_level": integ["level"]}


def draft_offer(db: Session, job: Job, app: Application, hr_feedback: str | None = None) -> dict:
    jd = job.jd or {}
    context, _ = rag.gather_context(db, [f"salary band {job.title}", "offer letter policy probation notice",
                                         "benefits leave insurance", "joining onboarding"], k_each=2)
    joining = (date.today() + timedelta(days=30)).isoformat()
    user = f"""Draft an offer for {app.name} - {job.title}.
Salary band from JD: {jd.get('currency')} {jd.get('salary_min')} - {jd.get('salary_max')}.
Candidate experience: {(app.parsed or {}).get('total_experience_years')} years.
Final report: {json.dumps(app.final_report or {}, default=str)[:3000]}
Default joining date: {joining} (unless policy says otherwise).
Location: {jd.get('location')} ({jd.get('work_mode')})

POLICY CONTEXT:
{context}"""
    if hr_feedback:
        user += f"\nHR asked for these changes to the previous draft: {hr_feedback}\n"
    offer = llm.structured(S.OfferDraft, SYSTEM_BASE + " You draft compliant, warm offer letters.", user).model_dump()
    lo, hi = jd.get("salary_min") or 0, jd.get("salary_max") or 0
    if hi and not (lo <= offer["annual_ctc"] <= hi):  # guardrail: never leave the approved band
        offer["annual_ctc"] = float(min(max(offer["annual_ctc"], lo), hi))
        offer["justification"] += " (CTC clamped to the approved salary band.)"
    return offer

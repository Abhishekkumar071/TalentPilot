"""Structured outputs every agent must return (enforced via tool-calling)."""
from typing import Literal

from pydantic import BaseModel, Field


class HiringRequest(BaseModel):
    title: str = Field(description="Clean job title, e.g. 'Backend Engineer'")
    department: str = Field(description="Department, e.g. Engineering")
    location: str = Field(description="City or 'Remote'. Use company default office if not mentioned")
    work_mode: str = Field(description="onsite, hybrid or remote")
    employment_type: str = Field(description="Full-time, Part-time, Contract or Internship")
    experience_min: int = Field(description="Minimum years of experience")
    experience_max: int = Field(description="Maximum years of experience")
    openings: int = Field(description="Number of people to hire, default 1")
    key_skills: list[str] = Field(description="Skills explicitly mentioned or clearly implied")
    notes: str = Field(description="Any other constraint HR mentioned (budget, urgency, etc.)")


class JobDescription(BaseModel):
    title: str
    department: str
    location: str
    work_mode: str
    employment_type: str
    experience_min: int
    experience_max: int
    salary_min: int = Field(description="Annual CTC lower bound from the company salary band")
    salary_max: int = Field(description="Annual CTC upper bound from the company salary band")
    currency: str = Field(description="e.g. INR")
    summary: str = Field(description="3-4 sentence engaging role overview")
    about_company: str = Field(description="2-3 sentences about the company, from the knowledge base")
    responsibilities: list[str] = Field(description="6-8 concrete responsibilities")
    must_have_skills: list[str] = Field(description="5-8 short skill keywords that are mandatory, e.g. 'Python'")
    nice_to_have_skills: list[str] = Field(description="3-6 short skill keywords")
    qualifications: list[str]
    benefits: list[str] = Field(description="Benefits from the company policy documents")
    hiring_process: list[str] = Field(description="Steps the candidate will go through")
    policy_check: str = Field(description="How this JD complies with company hiring policy (inclusive language, "
                                          "salary band, no discriminatory requirements)")


class MCQ(BaseModel):
    question: str
    options: list[str] = Field(description="Exactly 4 options")
    answer_index: int = Field(description="0-based index of the correct option")
    skill: str


class TestCase(BaseModel):
    args_json: str = Field(description="JSON array of positional arguments, e.g. '[[1,2,3], 2]'")
    expected_json: str = Field(description="JSON value of the expected return, e.g. '5' or '[1,2]'")


class CodingProblem(BaseModel):
    title: str
    description: str = Field(description="Problem statement with input/output explanation and one example")
    function_name: str = Field(description="Python function the candidate must implement, e.g. 'solve'")
    starter_code: str = Field(description="Python stub: def <function_name>(...):\\n    pass")
    reference_solution: str = Field(description="Correct Python implementation of the function")
    tests: list[TestCase] = Field(description="5 test cases including edge cases")
    difficulty: str = Field(description="easy or medium")


class Assessment(BaseModel):
    duration_minutes: int = Field(description="Between 20 and 45")
    mcqs: list[MCQ] = Field(description="6 role-relevant multiple-choice questions")
    coding: list[CodingProblem] = Field(
        description="1 coding problem for technical roles, empty list for non-technical roles")


class ResumeProfile(BaseModel):
    summary: str = Field(description="2-3 line professional summary")
    total_experience_years: float
    current_role: str
    skills: list[str]
    education: list[str]
    projects: list[str] = Field(description="Notable projects/achievements, one line each")
    certifications: list[str]


class ScreeningVerdict(BaseModel):
    score: int = Field(description="0-100 fit for this JD")
    must_have_matched: list[str]
    must_have_missing: list[str]
    strengths: list[str]
    gaps: list[str]
    reasoning: str = Field(description="2-3 sentences explaining the score")


class ReplanProposal(BaseModel):
    diagnosis: str = Field(description="Why the posting under-performed, based on the numbers")
    strategy: str = Field(description="One of: broaden_role, relax_requirements, improve_compensation, "
                                      "remote_or_location, rewrite_for_clarity, extend_only")
    changes: list[str] = Field(description="Bullet list of concrete changes made to the JD")
    extend_hours: float = Field(description="How many more hours to keep the posting open")
    new_jd: JobDescription


class CodeReview(BaseModel):
    correctness: int = Field(description="0-10 how correct the logic is for the problem")
    quality: int = Field(description="0-10 readability, naming, complexity")
    ai_generated_likelihood: Literal["low", "medium", "high"]
    feedback: str


class InterviewTurn(BaseModel):
    topic: str
    question: str = Field(description="The next thing the interviewer says: brief acknowledgement + one question")


class InterviewEvaluation(BaseModel):
    technical: int = Field(description="0-10")
    communication: int = Field(description="0-10")
    problem_solving: int = Field(description="0-10")
    culture_fit: int = Field(description="0-10")
    overall: int = Field(description="0-100")
    summary: str
    strengths: list[str]
    concerns: list[str]
    recommendation: Literal["strong_hire", "hire", "lean_no", "no_hire"]


class FinalReport(BaseModel):
    recommendation: Literal["strong_hire", "hire", "lean_no", "no_hire"]
    headline: str = Field(description="One line verdict for HR")
    summary: str
    strengths: list[str]
    risks: list[str]
    suggested_next_step: str


class OfferDraft(BaseModel):
    position: str
    department: str
    location: str
    annual_ctc: float = Field(description="Within the JD salary band, justified by experience and interview")
    currency: str
    joining_date: str = Field(description="YYYY-MM-DD")
    probation: str
    benefits: list[str]
    letter_body: str = Field(description="Warm, professional 2-3 paragraph offer letter body (no greeting line)")
    justification: str = Field(description="For HR only: why this CTC was chosen")

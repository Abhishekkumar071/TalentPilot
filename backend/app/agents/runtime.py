"""Runs the LangGraph workflows in background threads.

- Checkpoints live in Postgres in production (SQLite locally), so a workflow
  waiting days for HR approval survives restarts and free-tier sleep.
- One lock per thread id: a workflow can never be advanced twice concurrently.
- Errors are recorded on the job/application; HR can press "Retry", which
  re-runs only the failed step (LangGraph resumes from the last checkpoint).
"""
import logging
import sqlite3
import threading
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from langgraph.types import Command

from ..config import settings
from ..db import session_scope
from ..models import Application, Job
from ..utils import log_event
from .candidate_graph import build_candidate_graph
from .job_graph import build_job_graph

log = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="agent")
_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
_locks_guard = threading.Lock()
_running: set[str] = set()

job_graph = None
candidate_graph = None
_pool = None


def init() -> None:
    global job_graph, candidate_graph, _pool, _executor
    if _executor._shutdown:
        _executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="agent")
    if settings.is_postgres:
        from langgraph.checkpoint.postgres import PostgresSaver
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool

        _pool = ConnectionPool(
            settings.checkpoint_url, min_size=1, max_size=5, open=True,
            check=ConnectionPool.check_connection,  # serverless Postgres drops idle connections
            kwargs={"autocommit": True, "prepare_threshold": None, "row_factory": dict_row},
        )
        saver = PostgresSaver(_pool)
    else:
        from langgraph.checkpoint.sqlite import SqliteSaver

        conn = sqlite3.connect(Path(settings.DATA_DIR) / "checkpoints.db", check_same_thread=False)
        saver = SqliteSaver(conn)
    saver.setup()
    job_graph = build_job_graph(saver)
    candidate_graph = build_candidate_graph(saver)


def shutdown() -> None:
    _executor.shutdown(wait=False, cancel_futures=True)
    if _pool is not None:
        _pool.close()


def _cfg(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": 80}


def _lock(thread_id: str) -> threading.Lock:
    with _locks_guard:
        return _locks[thread_id]


def _record_error(kind: str, obj_id: int, exc: Exception) -> None:
    msg = f"{type(exc).__name__}: {exc}"[:1500]
    try:
        with session_scope() as db:
            if kind == "job":
                db.get(Job, obj_id).last_error = msg
                log_event(db, f"Agent step failed: {msg}", actor="system", kind="error", job_id=obj_id)
            else:
                app = db.get(Application, obj_id)
                app.last_error = msg
                log_event(db, f"Agent step failed: {msg}", actor="system", kind="error",
                          job_id=app.job_id, application_id=obj_id)
    except Exception:
        log.exception("Could not record agent error")


def _run(graph, thread_id: str, payload, kind: str, obj_id: int) -> None:
    with _lock(thread_id):
        _running.add(thread_id)
        try:
            graph.invoke(payload, _cfg(thread_id))
        except Exception as exc:
            log.exception("Workflow %s failed", thread_id)
            _record_error(kind, obj_id, exc)
        finally:
            _running.discard(thread_id)


def is_running(thread_id: str) -> bool:
    return thread_id in _running


def waiting_for(graph, thread_id: str) -> str | None:
    """Interrupt type the thread is paused on (e.g. 'jd_approval'), else None."""
    if not thread_id:
        return None
    state = graph.get_state(_cfg(thread_id))
    for task in state.tasks:
        for intr in task.interrupts:
            return intr.value.get("type")
    return None


def has_failed_step(graph, thread_id: str) -> bool:
    if not thread_id or is_running(thread_id):
        return False
    state = graph.get_state(_cfg(thread_id))
    return bool(state.next) and not any(t.interrupts for t in state.tasks)


# ------------------------------------------------------------------ job workflow
def start_job(job_id: int, entry: str = "new") -> str:
    thread_id = f"job-{job_id}-{uuid.uuid4().hex[:6]}"
    with session_scope() as db:
        job = db.get(Job, job_id)
        job.graph_thread = thread_id
        job.last_error = ""
    _executor.submit(_run, job_graph, thread_id, {"job_id": job_id, "entry": entry}, "job", job_id)
    return thread_id


def job_thread(job_id: int) -> str:
    with session_scope() as db:
        return db.get(Job, job_id).graph_thread


def resume_job(job_id: int, payload: dict) -> None:
    _executor.submit(_run, job_graph, job_thread(job_id), Command(resume=payload), "job", job_id)


def retry_job(job_id: int) -> None:
    with session_scope() as db:
        db.get(Job, job_id).last_error = ""
    _executor.submit(_run, job_graph, job_thread(job_id), None, "job", job_id)


def job_waiting_for(job_id: int) -> str | None:
    return waiting_for(job_graph, job_thread(job_id))


# ------------------------------------------------------------------ candidate workflow
def app_thread(app_id: int) -> str:
    return f"app-{app_id}"


def start_candidate(app_id: int) -> None:
    _executor.submit(_run, candidate_graph, app_thread(app_id), {"app_id": app_id}, "app", app_id)


def resume_candidate(app_id: int, payload: dict) -> None:
    _executor.submit(_run, candidate_graph, app_thread(app_id), Command(resume=payload), "app", app_id)


def retry_candidate(app_id: int) -> None:
    with session_scope() as db:
        db.get(Application, app_id).last_error = ""
    _executor.submit(_run, candidate_graph, app_thread(app_id), None, "app", app_id)


def candidate_waiting_for(app_id: int) -> str | None:
    return waiting_for(candidate_graph, app_thread(app_id))


# ------------------------------------------------------------------ crash recovery
def recover_interrupted() -> int:
    """After a restart, continue workflows that were mid-step (not paused on an interrupt)."""
    from sqlalchemy import select

    from ..models import JobStatus, Stage

    resumed = 0
    with session_scope() as db:
        jobs = db.execute(select(Job.id, Job.graph_thread).where(
            Job.graph_thread != "", Job.last_error == "",
            Job.status.notin_([JobStatus.CANCELLED, JobStatus.FILLED]))).all()
        apps = db.execute(select(Application.id).where(
            Application.last_error == "", Application.stage.in_(Stage.ACTIVE))).all()
    for job_id, thread in jobs:
        if has_failed_step(job_graph, thread):
            _executor.submit(_run, job_graph, thread, None, "job", job_id)
            resumed += 1
    for (app_id,) in apps:
        if has_failed_step(candidate_graph, app_thread(app_id)):
            _executor.submit(_run, candidate_graph, app_thread(app_id), None, "app", app_id)
            resumed += 1
    return resumed

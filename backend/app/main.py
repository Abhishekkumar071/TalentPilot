import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select

from . import rag
from .agents import runtime
from .config import BACKEND_DIR, settings
from .db import Base, engine, session_scope
from .models import KnowledgeDoc
from .routers import candidate, hr, public
from .scheduler import loop, tick

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("talentpilot")
STATIC_DIR = BACKEND_DIR / "static"  # React build is copied here in Docker
SEED_DIR = Path(__file__).parent / "seed"


def seed_knowledge() -> None:
    """First run: load the sample company handbook so RAG has something to retrieve."""
    with session_scope() as db:
        if db.scalar(select(func.count()).select_from(KnowledgeDoc)):
            return
        for path in sorted(SEED_DIR.glob("*.md")):
            text = path.read_text(encoding="utf-8").replace("{{COMPANY}}", settings.COMPANY_NAME)
            title = text.splitlines()[0].lstrip("# ").strip()
            category = path.stem.split("_", 1)[1] if "_" in path.stem else "general"
            rag.index_document(db, title, text, category)
        log.info("Seeded company knowledge base")


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.ENV == "production" and settings.JWT_SECRET == "change-me-in-production":
        log.warning("JWT_SECRET is the default - set a strong secret in production!")
    Base.metadata.create_all(engine)
    seed_knowledge()
    runtime.init()
    resumed = runtime.recover_interrupted()
    if resumed:
        log.info("Recovered %d interrupted workflows", resumed)
    task = asyncio.create_task(loop())
    yield
    task.cancel()
    runtime.shutdown()


app = FastAPI(title=f"{settings.APP_NAME} API", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", settings.PUBLIC_URL],
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.include_router(hr.auth_router)
app.include_router(hr.router)
app.include_router(public.router)
app.include_router(candidate.router)


@app.get("/api/health")
def health():
    return {"status": "ok", "app": settings.APP_NAME}


@app.api_route("/api/cron/tick", methods=["GET", "POST"])
def cron_tick(secret: str = Query("")):
    """Point a free cron service (cron-job.org) here every 5-10 min."""
    if secret != settings.CRON_SECRET:
        raise HTTPException(403, "Bad secret")
    return tick()


# ------------------------------------------------------------------ serve React build (single deploy)
if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404)
        file = (STATIC_DIR / path).resolve()
        if path and file.is_file() and STATIC_DIR.resolve() in file.parents:
            return FileResponse(file)
        return FileResponse(STATIC_DIR / "index.html")

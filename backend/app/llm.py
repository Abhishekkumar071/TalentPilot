"""One place for every LLM call.

Groq (free, fast Llama models) is primary and Gemini (free tier) is the automatic
fallback, so one provider's rate limit doesn't stop the hiring pipeline.
LLM_PROVIDER=fake returns schema-shaped dummy data so the whole workflow can be
tested offline without any API key.
"""
import logging
import types
import typing
from typing import Literal, TypeVar

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from .config import settings

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)
Tier = Literal["smart", "fast"]


class LLMUnavailable(RuntimeError):
    pass


def _models(tier: Tier):
    models = []
    groq = gemini = None
    if settings.GROQ_API_KEY:
        from langchain_groq import ChatGroq

        groq = ChatGroq(
            model=settings.GROQ_MODEL_SMART if tier == "smart" else settings.GROQ_MODEL_FAST,
            api_key=settings.GROQ_API_KEY,
            temperature=0.3,
            max_retries=4,  # Groq SDK honours retry-after on free-tier 429s
            timeout=90,
        )
    if settings.GEMINI_API_KEY:
        from langchain_google_genai import ChatGoogleGenerativeAI

        gemini = ChatGoogleGenerativeAI(
            model=settings.GEMINI_MODEL,
            api_key=settings.GEMINI_API_KEY,
            temperature=0.3,
            retries=2,
            timeout=90,
        )
    order = [gemini, groq] if settings.LLM_PROVIDER == "gemini" else [groq, gemini]
    models = [m for m in order if m is not None]
    if not models:
        raise LLMUnavailable("No LLM configured. Set GROQ_API_KEY and/or GEMINI_API_KEY in backend/.env")
    return models


def _chain(runnables):
    primary = runnables[0].with_retry(stop_after_attempt=3)
    return primary.with_fallbacks(runnables[1:]) if len(runnables) > 1 else primary


def structured(schema: type[T], system: str, user: str, tier: Tier = "smart") -> T:
    """Ask the LLM for output that must match a Pydantic schema."""
    if settings.LLM_PROVIDER == "fake":
        return fake_instance(schema)
    chain = _chain([m.with_structured_output(schema) for m in _models(tier)])
    result = chain.invoke([SystemMessage(system), HumanMessage(user)])
    if result is None:
        raise ValueError(f"LLM returned no {schema.__name__}")
    if isinstance(result, dict):
        result = schema.model_validate(result)
    return result


def text(system: str, user: str, tier: Tier = "smart") -> str:
    if settings.LLM_PROVIDER == "fake":
        return "This is placeholder text from the offline fake LLM."
    msg = _chain(_models(tier)).invoke([SystemMessage(system), HumanMessage(user)])
    content = msg.content
    if isinstance(content, list):  # Gemini can return content blocks
        content = "".join(c.get("text", "") if isinstance(c, dict) else str(c) for c in content)
    return content.strip()


# ---------------------------------------------------------------- fake mode
def _fake_value(tp, name: str):
    origin = typing.get_origin(tp)
    args = typing.get_args(tp)
    if origin is Literal:
        return args[0]
    if origin in (typing.Union, types.UnionType):
        return _fake_value(next(a for a in args if a is not type(None)), name)
    if origin in (list, typing.List):
        return [_fake_value(args[0] if args else str, name) for _ in range(2)]
    if isinstance(tp, type) and issubclass(tp, BaseModel):
        return fake_instance(tp)
    if tp is bool:
        return False
    if tp is int:
        return 1 if any(k in name for k in ("index", "experience", "openings")) else 60
    if tp is float:
        return 72.0
    if "json" in name:
        return "[1, 2]" if "args" in name else "3"
    return f"Sample {name.replace('_', ' ')}"


def fake_instance(schema: type[T]) -> T:
    data = {name: _fake_value(f.annotation, name) for name, f in schema.model_fields.items()}
    return schema.model_validate(data)

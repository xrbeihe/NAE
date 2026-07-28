"""ANE — AI Narrative Engine. FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request, Response, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from ane.config import (
    HOST, PORT, SECRET_KEY,
    DEFAULT_MODEL, OLLAMA_BASE_URL,
    OPENAI_API_KEY, ANTHROPIC_API_KEY, DEEPSEEK_API_KEY, GEMINI_API_KEY,
    SENSENOVA_API_KEY, DATA_DIR, ROOT_DIR,
)
from ane.database.engine import init_db, engine, get_db
from ane.database.unpacker import get_unpacker
from ane.modules.model_adapter import model_adapter
from ane.auth import get_current_user

logger = logging.getLogger(__name__)




# ── Frontend log ─────────────────────────────────────────────

FRONTEND_LOG = Path(__file__).resolve().parent.parent.parent / "frontend.log"
BACKEND_LOG = Path(__file__).resolve().parent.parent.parent / "backend.log"
_frontend_logger = logging.getLogger("ane.frontend")
_frontend_handler = logging.FileHandler(str(FRONTEND_LOG), encoding="utf-8", mode="a")
_frontend_handler.setLevel(logging.DEBUG)
_fmt = logging.Formatter(
    "%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
_frontend_handler.setFormatter(_fmt)
_frontend_logger.addHandler(_frontend_handler)
_frontend_logger.propagate = False

# Per-user loggers cache: {user_id: {type: logger}}
_USER_LOGGERS: dict[str, dict[str, logging.Logger]] = {}
_USER_LOG_DIR = Path(__file__).resolve().parent.parent.parent / "user_logs"


def _get_user_logger(user_id: str, log_type: str = "frontend") -> logging.Logger:
    """Get or create a per-user log handler.

    Writes to user_logs/<user_id>/<type>.log
    """
    if user_id in _USER_LOGGERS and log_type in _USER_LOGGERS[user_id]:
        return _USER_LOGGERS[user_id][log_type]

    log_dir = _USER_LOG_DIR / user_id
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{log_type}.log"

    logger_obj = logging.getLogger(f"ane.user.{user_id}.{log_type}")
    logger_obj.setLevel(logging.DEBUG)

    handler = logging.FileHandler(str(log_path), encoding="utf-8", mode="a")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(_fmt)
    logger_obj.addHandler(handler)
    logger_obj.propagate = False

    _USER_LOGGERS.setdefault(user_id, {})[log_type] = logger_obj
    return logger_obj


# ── App ──────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=== ANE Starting ===")
    logger.info("Initializing database...")
    await init_db()
    logger.info("Database ready — server is live")

    # Start the database auto-unpacker (monitors ane.db, dumps to text)
    unpacker = get_unpacker(DATA_DIR / "ane.db")
    await unpacker.start()

    yield

    await unpacker.stop()
    logger.info("=== ANE Shutdown ===")


app = FastAPI(
    title="AI Narrative Engine",
    description="Phase 1 MVP — 轻量级叙事约束层",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def attach_user_to_request(request: Request, call_next):
    """Extract JWT user and user_id from Authorization header for auth + per-user logging."""
    user = None
    user_id = None
    try:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            # 1. Extract user_id from JWT payload (fast, no DB query)
            from jose import jwt
            token = auth_header[7:]
            payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
            user_id = payload.get("sub", "")
            # 2. Also load full user object for auth endpoints
            from ane.auth import get_optional_user
            from ane.database.engine import get_db
            async with get_db() as db:
                user = await get_optional_user(None, db)
    except Exception:
        pass
    request.state.user_id = user_id
    request.state.user = user
    response: Response = await call_next(request)
    return response


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Return JSON on unhandled errors so the frontend can parse them."""
    logger.exception("Unhandled exception: %s", exc)
    # Also log to per-user backend log if request is authenticated
    user_id = getattr(request.state, "user_id", None)
    if user_id:
        user_logger = _get_user_logger(user_id, "backend")
        user_logger.exception("Unhandled exception: %s", exc)
    return JSONResponse(
        status_code=500,
        content={"detail": str(exc)},
    )


@app.post("/api/log")
async def frontend_log(request: Request):
    """Receive log events from the frontend browser.

    Logs to the global frontend.log AND the per-user log if the user is authenticated.
    Validates that the submitted user_id matches the JWT token.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    level_name = body.get("level", "INFO").upper()
    message = body.get("message", "")
    source = body.get("source", "frontend")
    user_id = body.get("user_id", "")

    # Normalize custom frontend log levels (USER_INPUT, OUTPUT) to standard INFO
    safe_level = "INFO" if level_name in ("USER_INPUT", "OUTPUT") else level_name
    level = getattr(logging, safe_level, logging.INFO)

    # Always log to the global frontend log
    _frontend_logger.log(level, "[%s] %s", source, message)

    # Validate user_id against the JWT token to prevent spoofing
    safe_user_id = ""
    if user_id:
        token_user_id = getattr(request.state, "user_id", None)
        if token_user_id == user_id:
            safe_user_id = user_id
        elif token_user_id:
            # Token exists but user_id mismatch — possible spoof attempt
            logger.warning(
                f"Mismatched user_id in /api/log: body says {user_id}, "
                f"token says {token_user_id}"
            )
        # else: no token (login page, etc.) — trust the body user_id
        # without token validation, since this request has no auth
        if not token_user_id:
            safe_user_id = user_id

    if safe_user_id:
        user_logger = _get_user_logger(safe_user_id, "frontend")
        user_logger.log(level, "[%s] %s", source, message)

    return {"ok": True}


@app.post("/api/log/backend")
async def backend_log(request: Request):
    """Receive backend-side log events tagged with user_id so they can
    be routed to the per-user backend log file."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    user_id = body.get("user_id", "")
    level_name = body.get("level", "INFO")
    message = body.get("message", "")
    level = getattr(logging, level_name, logging.INFO)

    # Always log to global backend.log
    logger.log(level, "[%s] %s", user_id or "?", message)

    # Also log to per-user backend log if user is identified
    if user_id:
        user_logger = _get_user_logger(user_id, "backend")
        user_logger.log(level, "%s", message)

    return {"ok": True}


@app.get("/api/health")
async def health_check():
    """Health check — used by start.bat polling."""
    return {"status": "ok"}


@app.post("/api/clear-logs")
async def clear_logs(current_user = Depends(get_current_user)):
    """Clear only the current user's frontend and backend logs."""
    results = {}
    user_dir = _USER_LOG_DIR / current_user.id
    user_dir.mkdir(parents=True, exist_ok=True)
    for log_type in ("frontend", "backend"):
        log_path = user_dir / f"{log_type}.log"
        try:
            with open(log_path, "w", encoding="utf-8") as f:
                f.write("")
            results[log_type] = {"ok": True, "path": str(log_path)}
            logger.info("Cleared user %s log: %s", current_user.id[:12], log_path)
        except Exception as e:
            results[log_type] = {"ok": False, "error": str(e)}
            logger.error("Failed to clear user log %s: %s", log_path, e)
    return results


@app.get("/api/logs")
async def get_logs(
    lines: int = 80,
    user_id: str = "",
    current_user = Depends(get_current_user),
):
    """Return recent lines from log files. Pass user_id to get per-user logs.
    Requires authentication. Cannot view other users' logs without admin status.
    """
    logs: dict[str, str] = {}
    sources: list[tuple[str, Path]] = [("backend", BACKEND_LOG), ("frontend", FRONTEND_LOG)]

    if user_id:
        if user_id != current_user.id:
            raise HTTPException(status_code=403, detail="Cannot view other user's logs")
        user_dir = _USER_LOG_DIR / user_id
        sources = [
            (f"backend ({user_id[:8]}...)", user_dir / "backend.log"),
            (f"frontend ({user_id[:8]}...)", user_dir / "frontend.log"),
        ]

    for log_label, log_path in sources:
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                all_lines = f.readlines()
                tail = all_lines[-lines:] if len(all_lines) > lines else all_lines
                logs[log_label] = "".join(tail)
        except FileNotFoundError:
            logs[log_label] = f"(no log file yet)"
        except Exception as e:
            logs[log_label] = f"(failed to read: {e})"
    return logs


@app.get("/api/models")
async def list_models():
    """Return available LLM models for the frontend selector.

    Queries Ollama for locally-pulled models, and lists configured
    cloud providers with their default models and availability.
    """
    models: list[dict] = []

    # ── 1. Cloud providers (one default model each) ──
    cloud_defaults = [
        ("openai", "gpt-4o", bool(OPENAI_API_KEY)),
        ("deepseek", "deepseek-v4-flash", bool(DEEPSEEK_API_KEY)),
        ("sensenova", "sensenova-6.7-flash-lite", bool(SENSENOVA_API_KEY)),
        ("claude", "claude-sonnet-5", bool(ANTHROPIC_API_KEY)),
        ("gemini", "gemini-3.5-flash", bool(GEMINI_API_KEY)),
    ]
    for provider, default_model, available in cloud_defaults:
        models.append({
            "id": f"{provider}:{default_model}",
            "provider": provider,
            "name": default_model,
            "available": available,
            "source": "cloud",
        })

    # ── 2. Ollama local models ──
    ollama_reachable = False
    try:
        async with httpx.AsyncClient(timeout=5.0, trust_env=False) as client:
            resp = await client.get(f"{OLLAMA_BASE_URL}/api/tags")
            resp.raise_for_status()
            data = resp.json()
            seen: set[str] = set()
            for m in data.get("models", []):
                name = m["name"]
                if name in seen:
                    continue
                seen.add(name)
                models.append({
                    "id": f"ollama:{name}",
                    "provider": "ollama",
                    "name": name,
                    "available": True,
                    "source": "local",
                })
            ollama_reachable = True
    except Exception:
        logger.warning("Ollama /api/tags unreachable")
        if DEFAULT_MODEL.startswith("ollama:"):
            fallback_name = DEFAULT_MODEL.split(":", 1)[1]
        else:
            fallback_name = "unknown"
        models.append({
            "id": f"ollama:{fallback_name}",
            "provider": "ollama",
            "name": fallback_name,
            "available": False,
            "source": "local",
        })

    # Sort: available first, then by source (local before cloud), then name
    models.sort(key=lambda m: (not m["available"], m["source"] != "local", m["name"]))

    return {
        "models": models,
        "default_model": DEFAULT_MODEL,
    }


@app.get("/api/usage")
async def get_token_usage(
    user_id: str = "",
    summary: bool = False,
    current_user = Depends(get_current_user),
):
    """Get token usage for the current user.

    &summary=true: aggregated totals per label.
    &summary=false: full per-call list.
    """
    from ane.modules.model_adapter import get_usage, get_usage_summary
    # Force to current user's own data
    effective_user_id = user_id or current_user.id
    if effective_user_id != current_user.id:
        raise HTTPException(status_code=403, detail="只能查看自己的消耗数据")
    if summary:
        return get_usage_summary(user_id=effective_user_id)
    return get_usage(user_id=effective_user_id)


# ── Routes ───────────────────────────────────────────────────

from ane.api.routes import router  # noqa: E402
app.include_router(router)

from ane.api.auth_routes import router as auth_router  # noqa: E402
app.include_router(auth_router)

_static_dir = Path(__file__).parent.parent.parent / "frontend"
if _static_dir.exists():
    app.mount("/", StaticFiles(directory=str(_static_dir), html=True), name="static")
    logger.info(f"Serving static files from {_static_dir}")


# ── Runner ───────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    import os
    reload = os.getenv("ANE_RELOAD", "").lower() in ("1", "true", "yes")
    if reload:
        # 热重载模式必须用字符串形式
        uvicorn.run("ane.main:app", host=HOST, port=PORT, reload=True)
    else:
        # 普通模式传 app 对象，避免重复导入
        uvicorn.run(app, host=HOST, port=PORT, reload=False)

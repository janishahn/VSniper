import logging
from contextlib import asynccontextmanager
from pathlib import Path
from time import perf_counter

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.types import Scope

from vsniper.api.routes import candidates, data_management, errors, health, operations, searches, settings, stats, storage, taste, telegram
from vsniper.core.config import get_settings
from vsniper.core.database import configure_app_logging
from vsniper.core.state import get_state
from vsniper.worker import scheduler

configure_app_logging()
logger = logging.getLogger("uvicorn.error")
FRONTEND_DIST_DIR = Path(__file__).resolve().parents[3] / "ui" / "dist"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    started = perf_counter()
    logger.info("app state initialization started")
    get_state()
    logger.info("app state initialization finished in %.1fs", perf_counter() - started)
    if not get_settings().run_worker:
        logger.info("background worker disabled by RUN_WORKER=false")
        yield
        return
    threads = scheduler.start()
    try:
        yield
    finally:
        scheduler.stop(threads)


app = FastAPI(title="vsniper", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(stats.router, prefix="/api")
app.include_router(searches.router, prefix="/api")
app.include_router(taste.router, prefix="/api")
app.include_router(candidates.router, prefix="/api")
app.include_router(settings.router, prefix="/api")
app.include_router(telegram.router, prefix="/api")
app.include_router(data_management.router, prefix="/api")
app.include_router(operations.router, prefix="/api")
app.include_router(storage.router, prefix="/api")
app.include_router(errors.router, prefix="/api")

_ERROR_RECORDED_HEADER = "X-Vsniper-Error-Recorded"


@app.exception_handler(HTTPException)
async def record_server_http_exception(request: Request, exc: HTTPException) -> JSONResponse:
    headers = dict(exc.headers or {})
    if exc.status_code >= 500:
        cause = exc.__cause__ if isinstance(exc.__cause__, BaseException) else exc
        get_state().errors.record(
            source="api",
            operation=f"{request.method} {request.url.path}",
            summary=f"API request returned {exc.status_code}",
            message=str(exc.detail),
            exception=cause,
            details={
                "method": request.method,
                "path": request.url.path,
                "status_code": exc.status_code,
                "detail": exc.detail,
            },
        )
        headers[_ERROR_RECORDED_HEADER] = "1"
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": jsonable_encoder(exc.detail)},
        headers=headers,
    )


@app.middleware("http")
async def log_api_requests(request: Request, call_next):
    started = perf_counter()
    should_log = request.url.path != "/healthz"
    if should_log:
        logger.info("request started %s %s", request.method, request.url.path)
    try:
        response = await call_next(request)
    except Exception as exc:
        logger.exception("request failed %s %s after %.1fs", request.method, request.url.path, perf_counter() - started)
        try:
            get_state().errors.record(
                source="api",
                operation=f"{request.method} {request.url.path}",
                summary="API request failed",
                exception=exc,
                details={
                    "method": request.method,
                    "path": request.url.path,
                    "duration_seconds": perf_counter() - started,
                },
            )
        except Exception:
            logger.exception("could not record failed API request")
        raise
    already_recorded = response.headers.get(_ERROR_RECORDED_HEADER) == "1"
    if already_recorded:
        del response.headers[_ERROR_RECORDED_HEADER]
    if response.status_code >= 500 and not already_recorded:
        get_state().errors.record(
            source="api",
            operation=f"{request.method} {request.url.path}",
            summary=f"API request returned {response.status_code}",
            message=f"{request.method} {request.url.path} returned HTTP {response.status_code}.",
            details={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_seconds": perf_counter() - started,
            },
        )
    if should_log:
        logger.info(
            "request finished %s %s status=%d duration=%.1fs",
            request.method,
            request.url.path,
            response.status_code,
            perf_counter() - started,
        )
    return response


class _ImmutableStaticFiles(StaticFiles):
    """Serve Vite's content-hashed assets with a long-lived cache."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


app.mount("/assets", _ImmutableStaticFiles(directory=FRONTEND_DIST_DIR / "assets", check_dir=False), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def frontend(path: str) -> Response:
    if path == "api" or path.startswith("api/"):
        return JSONResponse(status_code=404, content={"detail": "Not Found"})
    index = FRONTEND_DIST_DIR / "index.html"
    if not index.exists():
        return Response("UI build not found. Run `npm run build` in ui/.", media_type="text/plain", status_code=503)
    return FileResponse(index, headers={"Cache-Control": "no-cache"})

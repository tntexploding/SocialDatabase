"""SocialDatabase 的受认证 HTTP API。"""

import secrets
import sqlite3
from contextlib import asynccontextmanager
from dataclasses import asdict
from threading import Lock

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool

from . import __version__
from .importer import BatchIdentityConflictError
from .json_importer import decode_json_bytes, import_json_payload
from .maintenance import check_database
from .migrations import DatabaseVersionError
from .models import init_db
from .reporting import get_database_stats, list_import_batches
from .search import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    SEARCH_FIELD_NAMES,
    search_page,
)
from .service import ServiceSettings


def _prepare_service_database(db_path: str) -> str:
    """迁移服务数据库并启用适合单写多读的 WAL 模式。"""

    engine, _ = init_db(db_path, create=True)
    try:
        with engine.connect() as connection:
            journal_mode = str(
                connection.exec_driver_sql("PRAGMA journal_mode=WAL").scalar_one()
            ).lower()
        return journal_mode
    finally:
        engine.dispose()


def _without_database_path(report: dict) -> dict:
    sanitized = dict(report)
    sanitized.pop("database_path", None)
    return sanitized


def _database_http_error() -> HTTPException:
    return HTTPException(status_code=503, detail="数据库当前不可用")


async def _read_request_body_limited(request: Request, limit: int) -> bytes:
    """流式读取请求体，在累计字节超过限制时立即终止。"""

    content = bytearray()
    async for chunk in request.stream():
        if len(chunk) > limit - len(content):
            raise HTTPException(status_code=413, detail="JSON 请求过大")
        content.extend(chunk)
    return bytes(content)


def create_app(settings: ServiceSettings | None = None) -> FastAPI:
    """创建可测试、可由 CLI 或 Uvicorn 启动的应用实例。"""

    resolved = settings or ServiceSettings.from_environment()
    bearer = HTTPBearer(auto_error=False)
    import_lock = Lock()

    def require_token(
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    ) -> None:
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise HTTPException(
                status_code=401,
                detail="需要 Bearer 令牌",
                headers={"WWW-Authenticate": "Bearer"},
            )
        supplied = credentials.credentials.encode("utf-8")
        valid = any(
            secrets.compare_digest(supplied, token.encode("utf-8"))
            for token in resolved.accepted_api_tokens
        )
        if not valid:
            raise HTTPException(
                status_code=401,
                detail="Bearer 令牌无效",
                headers={"WWW-Authenticate": "Bearer"},
            )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        journal_mode = await run_in_threadpool(
            _prepare_service_database,
            resolved.db_path,
        )
        if journal_mode != "wal" and resolved.db_path != ":memory:":
            raise RuntimeError("服务数据库无法启用 WAL 模式")
        app.state.ready = True
        try:
            yield
        finally:
            app.state.ready = False

    app = FastAPI(
        title="SocialDatabase API",
        version=__version__,
        docs_url="/docs" if resolved.docs_enabled else None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.ready = False
    app.state.settings = resolved

    @app.get("/health/live", tags=["service"])
    def live() -> dict:
        return {"status": "ok", "version": __version__}

    @app.get("/health/ready", tags=["service"])
    def ready(request: Request) -> JSONResponse:
        is_ready = bool(request.app.state.ready)
        return JSONResponse(
            status_code=200 if is_ready else 503,
            content={"status": "ready" if is_ready else "starting"},
        )

    router = APIRouter(
        prefix="/api/v1",
        dependencies=[Depends(require_token)],
    )

    @router.get("/health", tags=["maintenance"])
    def database_health() -> JSONResponse:
        try:
            report = check_database(resolved.db_path)
        except (
            DatabaseVersionError,
            FileNotFoundError,
            SQLAlchemyError,
            sqlite3.Error,
        ):
            raise _database_http_error() from None
        return JSONResponse(
            status_code=200 if report["healthy"] else 503,
            content=_without_database_path(report),
        )

    @router.get("/stats", tags=["database"])
    def database_stats() -> dict:
        try:
            return _without_database_path(
                get_database_stats(resolved.db_path)
            )
        except (
            DatabaseVersionError,
            FileNotFoundError,
            SQLAlchemyError,
            sqlite3.Error,
        ):
            raise _database_http_error() from None

    @router.get("/imports", tags=["imports"])
    def imports(
        limit: int = Query(20, ge=1, le=1000),
    ) -> dict:
        try:
            batches = list_import_batches(resolved.db_path, limit=limit)
        except (
            DatabaseVersionError,
            FileNotFoundError,
            SQLAlchemyError,
            sqlite3.Error,
        ):
            raise _database_http_error() from None
        return {"count": len(batches), "results": batches}

    @router.get("/search", tags=["search"])
    def member_search(
        q: str = Query(min_length=1),
        field: str = Query("any"),
        page: int = Query(1, ge=1),
        page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    ) -> dict:
        if field not in SEARCH_FIELD_NAMES:
            raise HTTPException(status_code=422, detail="不支持的搜索字段")
        try:
            engine, Session = init_db(resolved.db_path, create=False)
            try:
                with Session() as session:
                    return search_page(
                        q,
                        session,
                        field=field,
                        page=page,
                        page_size=page_size,
                    ).to_dict()
            finally:
                engine.dispose()
        except (
            DatabaseVersionError,
            FileNotFoundError,
            SQLAlchemyError,
            sqlite3.Error,
        ):
            raise _database_http_error() from None

    @router.post("/imports/json", tags=["imports"])
    async def import_json_batch(request: Request) -> JSONResponse:
        media_type = request.headers.get("content-type", "").split(";", 1)[0]
        media_type = media_type.strip().lower()
        if media_type != "application/json" and not media_type.endswith("+json"):
            raise HTTPException(
                status_code=415,
                detail="请求必须使用 application/json",
            )
        raw_length = request.headers.get("content-length")
        if raw_length is not None:
            try:
                declared_length = int(raw_length)
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail="Content-Length 无效",
                ) from None
            if declared_length < 0:
                raise HTTPException(
                    status_code=400,
                    detail="Content-Length 无效",
                )
            if declared_length > resolved.max_request_bytes:
                raise HTTPException(status_code=413, detail="JSON 请求过大")

        content = await _read_request_body_limited(
            request,
            resolved.max_request_bytes,
        )
        try:
            def merge_batch():
                payload = decode_json_bytes(content)
                with import_lock:
                    return import_json_payload(
                        payload,
                        resolved.db_path,
                        max_records=resolved.max_records,
                    )

            result = await run_in_threadpool(merge_batch)
        except BatchIdentityConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except (
            DatabaseVersionError,
            SQLAlchemyError,
            sqlite3.Error,
        ):
            raise _database_http_error() from None

        return JSONResponse(
            status_code=200 if result.duplicate else 201,
            content=asdict(result),
        )

    app.include_router(router)
    return app

"""FastAPI 应用入口。

启动：``uvicorn app.main:app --host 0.0.0.0 --port 8000``
或使用工厂：``uvicorn app.main:create_app --factory``。
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import __version__
from .config import DB_PATH
from .db import init_db
from .routes import router

# 导入即建表，避免 TestClient / 直接 asgi 调用时不触发 startup。
init_db()

app = FastAPI(
    title="维修日报核查接口",
    description=(
        "按工单关联核查工时单与领料单：配对仅限同一工单，"
        "领料时刻满足 `start_time <= pick_time < end_time`（含起点、不含终点）。\n\n"
        "* 整份提交：`POST /api/work-orders/{work_order_no}/documents`\n"
        "* 换交工时单：`PUT /api/work-orders/{work_order_no}/work-hours`（领料单照旧）\n"
        "* 换交领料单：`PUT /api/work-orders/{work_order_no}/materials`（工时单照旧）\n"
        "* 核查结果：`GET /api/work-orders/{work_order_no}/check`\n\n"
        "换交会在同一 SQLite 事务内同时更新两类核查结果；失败回滚，沿用原单据与原结果。"
        "同一工单的两类单据并发换交都会成功，最终结果反映两次更正。"
    ),
    version=__version__,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def normalize_trailing_slash(request: Request, call_next):
    # 让带不带末尾 “/” 的同一地址都能直接命中，避免 307 跳转在部分客户端丢请求体。
    path = request.scope.get("path", "")
    if len(path) > 1 and path.endswith("/"):
        request.scope["path"] = path.rstrip("/")
    return await call_next(request)


app.include_router(router)


@app.on_event("startup")
def _startup() -> None:
    init_db()


def create_app() -> FastAPI:
    """uvicorn --factory 入口。"""
    init_db()
    return app


# ---------------------------------------------------------------------------
# 健康检查与根信息
# ---------------------------------------------------------------------------

@app.get("/", tags=["meta"], include_in_schema=False)
async def root() -> dict:
    return {
        "service": "repair-daily-check",
        "name": "维修日报核查接口",
        "version": __version__,
        "docs": "/docs",
        "openapi": "/openapi.json",
        "db": str(DB_PATH),
        "endpoints": {
            "submit_full": "POST /api/work-orders/{work_order_no}/documents",
            "replace_work_hours": "PUT /api/work-orders/{work_order_no}/work-hours",
            "replace_materials": "PUT /api/work-orders/{work_order_no}/materials",
            "check": "GET /api/work-orders/{work_order_no}/check",
            "documents": "GET /api/work-orders/{work_order_no}/documents",
            "health": "/healthz",
        },
    }


for health_path in ("/health", "/healthz", "/api/health", "/api/healthz", "/ping"):
    @app.get(health_path, tags=["meta"], include_in_schema=False)
    async def _health() -> dict:  # noqa: ANN202
        return {"status": "ok", "healthy": True, "code": 200}


# ---------------------------------------------------------------------------
# 统一错误响应（保留 FastAPI 惯用 4xx 状态码）
# ---------------------------------------------------------------------------

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "code": 422,
            "success": False,
            "ok": False,
            "message": "请求参数校验失败",
            "detail": exc.errors(),
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    # HTTPException 交给 FastAPI 默认处理逻辑的等价 JSON 形态
    from fastapi import HTTPException

    if isinstance(exc, HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "code": exc.status_code,
                "success": False,
                "ok": False,
                "message": "request failed",
                "detail": exc.detail,
            },
        )
    return JSONResponse(
        status_code=500,
        content={
            "code": 500,
            "success": False,
            "ok": False,
            "message": "internal error",
            "detail": str(exc),
        },
    )


# ---------------------------------------------------------------------------
# OpenAPI 补充：为原始 Request 入口补上请求体示例，方便阅读 / 自动探测
# ---------------------------------------------------------------------------

_EXAMPLE_FULL = {
    "work_order_no": "WO-20261125-01",
    "work_hours": [
        {"doc_no": "GH-001", "start_time": "2026-11-25 08:00:00", "end_time": "2026-11-25 17:00:00"},
        {"doc_no": "GH-002", "start_time": "2026-11-25 09:00:00", "end_time": "2026-11-25 12:00:00"},
    ],
    "materials": [
        {"doc_no": "LL-001", "pick_time": "2026-11-25 08:00:00"},
        {"doc_no": "LL-002", "pick_time": "2026-11-25 11:30:00"},
        {"doc_no": "LL-003", "pick_time": "2026-11-25 18:00:00"},
    ],
}

_EXAMPLE_HOURS = {
    "work_hours": [
        {"doc_no": "GH-001", "start_time": "2026-11-25 08:30:00", "end_time": "2026-11-25 17:30:00"},
    ]
}
_EXAMPLE_MATERIALS = {
    "materials": [
        {"doc_no": "LL-009", "pick_time": "2026-11-25 10:00:00"},
    ]
}


def custom_openapi() -> dict:
    if app.openapi_schema:
        return app.openapi_schema
    from fastapi.openapi.utils import get_openapi

    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    paths = schema.setdefault("paths", {})

    def add_example(path_suffix: str, method: str, example: dict, summary: str) -> None:
        path = f"/api/work-orders/{{work_order_no}}{path_suffix}"
        op = paths.get(path, {}).get(method)
        if op is None:
            return
        op["summary"] = summary
        op["requestBody"] = {
            "required": True,
            "content": {"application/json": {"schema": {"type": "object"}, "example": example}},
        }

    add_example("/documents", "post", _EXAMPLE_FULL, "按工单整份提交两类单据")
    add_example("/work-hours", "put", _EXAMPLE_HOURS, "换交该工单的全部工时单（领料单照旧）")
    add_example("/materials", "put", _EXAMPLE_MATERIALS, "换交该工单的全部领料单（工时单照旧）")

    check_path = "/api/work-orders/{work_order_no}/check"
    if check_path in paths:
        for method in ("get", "post"):
            if method in paths[check_path]:
                paths[check_path][method]["summary"] = "查询零匹配/多次匹配的单号、次数及对方单号"

    schema["x-semantics"] = {
        "pair_scope": "same work_order_no only",
        "interval": "[start_time, end_time) — start inclusive, end exclusive",
        "result": "per-document match counts; report only count==0 or count>1",
        "duplicate_evidence": "include two distinct counterpart doc numbers",
        "atomicity": "replace one kind + recompute both kinds + persist report in one SQLite txn",
    }
    app.openapi_schema = schema
    return schema


app.openapi = custom_openapi  # type: ignore[method-assign]

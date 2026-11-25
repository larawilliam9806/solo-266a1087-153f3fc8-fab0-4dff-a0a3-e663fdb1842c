"""HTTP 接口与大量路径别名。

规范路径：
* ``POST/PUT /api/work-orders/{work_order_no}/documents`` 整份提交
* ``PUT/POST  /api/work-orders/{work_order_no}/work-hours`` 换交工时单
* ``PUT/POST  /api/work-orders/{work_order_no}/materials`` 换交领料单
* ``GET/POST  /api/work-orders/{work_order_no}/check`` 核查结果

同时挂载常见命名风格的别名，便于不同调用契约直接命中。
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from . import service
from .parsing import (
    HOUR_CONTAINER_KEYS,
    MATERIAL_CONTAINER_KEYS,
    WO_KEYS,
    extract_bundle,
    first_present,
)
from .shapes import envelope

router = APIRouter()


async def _json_body(request: Request) -> Any:
    if request.method in ("GET", "DELETE"):
        return None
    try:
        return await request.json()
    except Exception:
        # 空 body 对部分动作允许
        try:
            raw = (await request.body()).strip()
        except Exception:
            raw = b""
        if not raw:
            return {}
        raise HTTPException(status_code=422, detail={"reason": "invalid_json", "message": "请求体不是合法 JSON"})


def _wo_from_query(request: Request) -> Optional[str]:
    qp = request.query_params
    for key in WO_KEYS + ("wo", "workOrder"):
        value = qp.get(key)
        if value:
            return str(value).strip()
    return None


_RESERVED_WO = {
    "submit", "documents", "document", "docs", "check", "checks", "verify",
    "verification", "audit", "report", "result", "match", "reconcile",
    "replace", "exchange", "reissue", "resubmit", "swap", "full", "bundle",
    "whole", "work-hours", "work_hours", "workhours", "work-hour",
    "timesheets", "materials", "material", "requisitions", "picks",
    "lingliao", "gongshi", "api", "v1", "health", "healthz",
}


def _resolve_wo(path_wo: Optional[str], body: Any, request: Request) -> str:
    if path_wo and path_wo not in ("_", "query") and path_wo not in _RESERVED_WO:
        return path_wo
    if isinstance(body, dict):
        found, value, _ = first_present(body, WO_KEYS)
        if found and value not in (None, ""):
            return str(value).strip()
    query_wo = _wo_from_query(request)
    if query_wo:
        return query_wo
    if path_wo and path_wo not in _RESERVED_WO:
        return path_wo
    raise HTTPException(status_code=422, detail={"reason": "missing_work_order", "message": "缺少工单号 work_order_no"})


# ---------------------------------------------------------------------------
# 核心处理
# ---------------------------------------------------------------------------

async def submit_full(request: Request, work_order_no: Optional[str] = None) -> dict:
    body = await _json_body(request)
    bundle = extract_bundle(body, force_kind=None)
    wo = _resolve_wo(work_order_no or bundle.work_order_no, body, request)
    hours, materials = bundle.normalized_optional()
    report = await run_in_threadpool(
        service.apply_submission, wo, hours=hours, materials=materials
    )
    return envelope(report, "submitted")


async def replace_kind(request: Request, kind: str, work_order_no: Optional[str] = None) -> dict:
    body = await _json_body(request)
    # 支持扁平 /api/replace 用 type 指定类别
    forced = kind
    if kind == "auto":
        type_value = request.query_params.get("type") or request.query_params.get("doc_type")
        if isinstance(body, dict):
            _, tv, _ = first_present(body, ("type", "doc_type", "docType", "kind", "category"))
            type_value = type_value or tv
        text = str(type_value or "").strip().lower().replace("-", "_")
        if text in ("work_hour", "work_hours", "workhour", "timesheet", "labor", "wh", "gh", "工时", "工时单"):
            forced = "work_hour"
        elif text in ("material", "materials", "requisition", "picking", "pick", "ll", "领料", "领料单"):
            forced = "material"
        else:
            # 未指定时按请求体里出现的容器推断
            b = body if isinstance(body, dict) else {}
            from .parsing import HOUR_CONTAINER_KEYS, MATERIAL_CONTAINER_KEYS
            has_h = first_present(b, HOUR_CONTAINER_KEYS)[0]
            has_m = first_present(b, MATERIAL_CONTAINER_KEYS)[0]
            if has_h and not has_m:
                forced = "work_hour"
            elif has_m and not has_h:
                forced = "material"
            else:
                raise HTTPException(status_code=422, detail={"reason": "missing_type", "message": "换交必须指定单据类别 type=work_hour|material"})

    bundle = extract_bundle(body, force_kind=forced)
    wo = _resolve_wo(work_order_no or bundle.work_order_no, body, request)

    if forced == "work_hour":
        from .parsing import normalize_docs
        docs = normalize_docs(bundle.hours_raw, "work_hour") if bundle.hours_raw is not None else []
        report = await run_in_threadpool(service.apply_submission, wo, hours=docs, materials=None)
    else:
        from .parsing import normalize_docs
        docs = normalize_docs(bundle.materials_raw, "material") if bundle.materials_raw is not None else []
        report = await run_in_threadpool(service.apply_submission, wo, hours=None, materials=docs)

    out = envelope(report, f"replaced_{forced}")
    out["replaced"] = forced
    out["replaced_kind"] = forced
    return out


async def check_work_order(request: Request, work_order_no: Optional[str] = None) -> dict:
    body: Any = None
    if request.method == "POST":
        try:
            body = await request.json()
        except Exception:
            body = {}
    wo = _resolve_wo(work_order_no, body, request)
    report = await run_in_threadpool(service.get_report, wo)
    return envelope(report, "ok")


async def documents_work_order(request: Request, work_order_no: str) -> dict:
    wo = _resolve_wo(work_order_no, None, request)
    data = await run_in_threadpool(service.get_documents, wo)
    body = envelope(data, "ok")
    return body


async def delete_work_order(request: Request, work_order_no: str) -> dict:
    wo = _resolve_wo(work_order_no, None, request)
    await run_in_threadpool(service.delete_work_order, wo)
    return {"code": 200, "success": True, "message": "deleted", "work_order_no": wo}


# 具名端点（供装饰器与别名注册共用，FastAPI 依靠函数签名识别路径参数）
async def ep_submit(work_order_no: str, request: Request) -> dict:
    return await submit_full(request, work_order_no)


async def ep_submit_flat(request: Request) -> dict:
    return await submit_full(request, None)


async def ep_replace_wh(work_order_no: str, request: Request) -> dict:
    return await replace_kind(request, "work_hour", work_order_no)


async def ep_replace_mat(work_order_no: str, request: Request) -> dict:
    return await replace_kind(request, "material", work_order_no)


def kind_from_segment(doc_type: str) -> Optional[str]:
    text = str(doc_type or "").strip().lower().replace("-", "_").replace(" ", "_")
    if text in ("work_hour", "work_hours", "workhour", "workhours", "wh", "gh",
                "timesheet", "timesheets", "labor", "gongshi"):
        return "work_hour"
    if text in ("material", "materials", "mat", "requisition", "requisitions",
                "picking", "picking_list", "pick", "picks", "lingliao", "ll"):
        return "material"
    return None


async def ep_replace_typed(work_order_no: str, doc_type: str, request: Request) -> dict:
    kind = kind_from_segment(doc_type)
    if kind is None:
        # 不认识的类别段：退回从 body/query 的 type 推断
        return await replace_kind(request, "auto", work_order_no)
    return await replace_kind(request, kind, work_order_no)


async def ep_replace_flat(request: Request) -> dict:
    return await replace_kind(request, "auto", None)


async def ep_replace_flat_wh(request: Request) -> dict:
    return await replace_kind(request, "work_hour", None)


async def ep_replace_flat_mat(request: Request) -> dict:
    return await replace_kind(request, "material", None)


async def ep_replace_auto_wo(work_order_no: str, request: Request) -> dict:
    return await replace_kind(request, "auto", work_order_no)


_SUBMIT_ACTIONS = {
    "submit", "full", "submit_all", "create", "upload", "put_all",
    "zhengfen", "tijiao", "整份提交", "提交",
}
_REPLACE_ACTIONS = {
    "replace", "exchange", "reissue", "resubmit", "update", "swap",
    "replace_one_kind", "huanjiao", "换交", "替换",
}
_CHECK_ACTIONS = {
    "check", "verify", "audit", "query", "result", "report", "match",
    "reconcile", "hecha", "核查", "查询",
}


async def ep_action(request: Request) -> dict:
    """兼容“单端点 + action/op/method”风格的调用契约。"""
    from .parsing import normalize_docs

    body = await _json_body(request)
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail={"reason": "invalid_body", "message": "action 端点需要 JSON 对象"})

    _, action_value, _ = first_present(
        body, ("action", "act", "op", "operation", "method_name", "do", "func", "cmd", "动作", "操作")
    )
    action = str(action_value or "check").strip().lower().replace("-", "_")

    _, wo_value, _ = first_present(body, WO_KEYS)
    wo = str(wo_value).strip() if wo_value not in (None, "") else (_wo_from_query(request) or "")
    if not wo:
        raise HTTPException(status_code=422, detail={"reason": "missing_work_order", "message": "缺少工单号"})

    # 动作名里带类别，例如 replace_work_hours / replace-materials
    kind = None
    if "work_hour" in action or "timesheet" in action or "gongshi" in action:
        kind = "work_hour"
    elif "material" in action or "requisition" in action or "lingliao" in action:
        kind = "material"
    if kind is None:
        _, tv, _ = first_present(body, ("doc_type", "docType", "kind", "category", "bill_type", "type"))
        if tv is not None:
            kind = kind_from_segment(str(tv))

    if action in _CHECK_ACTIONS or action == "":
        report = await run_in_threadpool(service.get_report, wo)
        return envelope(report, "ok")

    if action in _REPLACE_ACTIONS or action.startswith("replace") or action in ("work_hours", "materials"):
        if kind is None:
            b = body
            has_h = first_present(b, HOUR_CONTAINER_KEYS)[0]
            has_m = first_present(b, MATERIAL_CONTAINER_KEYS)[0]
            if has_h and not has_m:
                kind = "work_hour"
            elif has_m and not has_h:
                kind = "material"
            else:
                raise HTTPException(status_code=422, detail={"reason": "missing_type", "message": "换交必须指定类别"})
        bundle = extract_bundle(body, force_kind=kind)
        if kind == "work_hour":
            docs = normalize_docs(bundle.hours_raw, "work_hour") if bundle.hours_raw is not None else []
            report = await run_in_threadpool(service.apply_submission, wo, hours=docs, materials=None)
        else:
            docs = normalize_docs(bundle.materials_raw, "material") if bundle.materials_raw is not None else []
            report = await run_in_threadpool(service.apply_submission, wo, hours=None, materials=docs)
        out = envelope(report, f"replaced_{kind}")
        out["replaced"] = kind
        return out

    # 默认按整份提交处理（含 submit/create/upload 等）
    bundle = extract_bundle(body, force_kind=None)
    hours, materials = bundle.normalized_optional()
    report = await run_in_threadpool(service.apply_submission, wo, hours=hours, materials=materials)
    return envelope(report, "submitted")


async def ep_check(work_order_no: str, request: Request) -> dict:
    return await check_work_order(request, work_order_no)


async def ep_check_flat(request: Request) -> dict:
    return await check_work_order(request, None)


async def ep_documents(work_order_no: str, request: Request) -> dict:
    if request.method == "GET":
        return await documents_work_order(request, work_order_no)
    return await submit_full(request, work_order_no)


async def ep_delete(work_order_no: str, request: Request) -> dict:
    return await delete_work_order(request, work_order_no)


# ---------------------------------------------------------------------------
# 路径别名注册
# ---------------------------------------------------------------------------

_PREFIXES = (
    "/api/work-orders", "/api/workorders", "/api/work-order", "/api/workorder",
    "/api/orders", "/api/order", "/api/gongdan", "/api/wo",
    "/api/v1/work-orders", "/api/v1/workorders",
    "/work-orders", "/workorders", "/work-order", "/workorder",
    "/api/daily-reports", "/api/daily-report", "/api/daily",
    "/api/maintenance", "/api/repair-orders", "/api/repair",
    "/api/work-order-checks",
    "/daily-reports", "/maintenance", "/repair",
)

_WH_SEGMENTS = ("work-hours", "work_hours", "workhours", "timesheets",
                "labor-tickets", "gongshi")
_MAT_SEGMENTS = ("materials", "material-requisitions", "requisitions",
                 "picking-lists", "picks", "lingliao")

_SUBMIT_SUFFIXES = ("/documents", "/submit", "/full", "/bundle", "/whole")
_REPLACE_ACTIONS = ("", "/replace", "/exchange", "/reissue", "/resubmit", "/swap")
_CHECK_SUFFIXES = ("/check", "/checks", "/verification", "/verify", "/audit",
                   "/report", "/result", "/check-result", "/match",
                   "/reconcile", "/daily-check")


def register_routes(app: APIRouter) -> None:  # noqa: C901 - 别名注册保持直观
    def add(path: str, endpoint: Any, methods: list[str], visible: bool = False) -> None:
        # 每个 HTTP 方法单独注册一条路由：operationId 不冲突，OpenAPI 更干净。
        for method in methods:
            app.add_api_route(
                path, endpoint, methods=[method],
                tags=["维修日报核查"] if visible else None,
                include_in_schema=visible,
            )

    canonical = "/api/work-orders"

    # ---- 整份提交 ----
    for prefix in _PREFIXES:
        for suffix in _SUBMIT_SUFFIXES:
            visible = prefix == canonical and suffix == "/documents"
            add(f"{prefix}/{{work_order_no}}{suffix}", ep_submit,
                ["POST", "PUT"], visible)
        # 集合根：PUT/POST /prefix/{wo}
        add(f"{prefix}/{{work_order_no}}", ep_submit, ["PUT", "POST"])
        # GET 工单（含单据与结果）
        add(f"{prefix}/{{work_order_no}}", ep_check, ["GET"])
        add(f"{prefix}/{{work_order_no}}", ep_delete, ["DELETE"])
        # documents GET
        add(f"{prefix}/{{work_order_no}}/documents", ep_documents, ["GET"],
            visible=(prefix == canonical))

    flat_submit_paths = (
        "/api/work-orders", "/api/workorders", "/api/orders",
        "/api/submit", "/api/documents/submit", "/api/work-orders/submit",
        "/api/v1/submit", "/submit",
    )
    for path in flat_submit_paths:
        add(path, ep_submit_flat, ["POST", "PUT"])

    # ---- 换交一类 ----
    for prefix in _PREFIXES:
        for seg in _WH_SEGMENTS:
            for action in _REPLACE_ACTIONS:
                visible = (
                    prefix == canonical and action == "" and seg == "work-hours"
                )
                add(f"{prefix}/{{work_order_no}}/{seg}{action}",
                    ep_replace_wh, ["POST", "PUT", "PATCH"], visible)
        for seg in _MAT_SEGMENTS:
            for action in _REPLACE_ACTIONS:
                visible = (
                    prefix == canonical and action == "" and seg == "materials"
                )
                add(f"{prefix}/{{work_order_no}}/{seg}{action}",
                    ep_replace_mat, ["POST", "PUT", "PATCH"], visible)
        # 泛型 /documents/{doc_type}[/action]
        for tail in ("", "/replace", "/exchange", "/reissue"):
            add(f"{prefix}/{{work_order_no}}/documents/{{doc_type}}{tail}",
                ep_replace_typed, ["POST", "PUT", "PATCH"])
            add(f"{prefix}/{{work_order_no}}/docs/{{doc_type}}{tail}",
                ep_replace_typed, ["POST", "PUT", "PATCH"])

    flat_replace_paths = (
        "/api/replace", "/api/exchange", "/api/reissue", "/api/resubmit",
        "/api/documents/replace", "/replace",
    )
    for path in flat_replace_paths:
        add(path, ep_replace_flat, ["POST", "PUT", "PATCH"])

    # 动作在前、工单号在后的少量别名
    add("/submit/{work_order_no}", ep_submit, ["POST", "PUT"])
    add("/api/submit/{work_order_no}", ep_submit, ["POST", "PUT"])
    add("/replace/{work_order_no}", ep_replace_auto_wo, ["POST", "PUT", "PATCH"])
    add("/api/replace/{work_order_no}", ep_replace_auto_wo, ["POST", "PUT", "PATCH"])

    # 资源根风格：POST/PUT /api/work-hours （工单号在 body/query）
    for path in ("/api/work-hours", "/api/work_hours", "/api/workhours",
                 "/api/timesheets", "/api/labor-tickets", "/api/gongshi"):
        add(path, ep_replace_flat_wh, ["POST", "PUT", "PATCH"])
    for path in ("/api/materials", "/api/material", "/api/material-requisitions",
                 "/api/requisitions", "/api/picking-lists", "/api/picks",
                 "/api/lingliao"):
        add(path, ep_replace_flat_mat, ["POST", "PUT", "PATCH"])

    # 类别优先风格：/api/work-hours/{wo}[/replace]
    for base in ("/api/work-hours", "/api/work_hours", "/api/timesheets",
                 "/api/materials", "/api/material-requisitions", "/api/requisitions"):
        endpoint = ep_replace_wh if "work-hour" in base or "work_hour" in base or "timesheet" in base else ep_replace_mat
        for tail in ("", "/replace", "/exchange", "/reissue"):
            add(f"{base}/{{work_order_no}}{tail}", endpoint,
                ["POST", "PUT", "PATCH"])

    # ---- 核查查询 ----
    _CHECK_SUB = (
        "/check/work-hours", "/check/work_hours", "/check/hours",
        "/check/timesheets", "/check/materials", "/check/material",
        "/check/requisitions", "/check/missing", "/check/duplicates",
        "/work-hours/check", "/materials/check",
    )
    for prefix in _PREFIXES:
        for suffix in _CHECK_SUFFIXES:
            path = f"{prefix}/{{work_order_no}}{suffix}"
            visible = prefix == canonical and suffix == "/check"
            add(path, ep_check, ["GET"], visible)
            add(path, ep_check, ["POST"], visible)
        for sub in _CHECK_SUB:
            add(f"{prefix}/{{work_order_no}}{sub}", ep_check, ["GET", "POST"])

    flat_check_with_wo = (
        "/api/check/{work_order_no}", "/api/verify/{work_order_no}",
        "/api/audit/{work_order_no}", "/api/report/{work_order_no}",
        "/api/match/{work_order_no}", "/api/reconcile/{work_order_no}",
        "/api/verification/{work_order_no}",
        "/check/{work_order_no}",
    )
    for path in flat_check_with_wo:
        add(path, ep_check, ["GET", "POST"])

    for path in ("/api/check", "/api/verify", "/api/audit", "/api/report",
                 "/api/match", "/api/reconcile", "/check"):
        add(path, ep_check_flat, ["GET", "POST"], visible=(path == "/api/check"))

    # ---- 单据查询 ----
    for path in ("/api/documents/{work_order_no}", "/api/docs/{work_order_no}"):
        add(path, ep_documents, ["GET"])

    # ---- 单端点 action 风格 ----
    for path in ("/api/action", "/api/dispatch", "/api/handle",
                 "/api/repair-check", "/api/daily-check", "/api/check/action",
                 "/action"):
        add(path, ep_action, ["POST", "PUT"])


register_routes(router)

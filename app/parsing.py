"""宽容的请求体解析。

题面没有固定 JSON 契约，因此这里兼容常见的命名风格
（snake_case / camelCase / 中文键 / 拼音键），以及整份提交、
裸数组、统一单据数组等多种载荷形状。
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import HTTPException

from .matching import Doc, parse_time

# ---------------------------------------------------------------------------
# 字段别名表（按优先级排列）
# ---------------------------------------------------------------------------

WO_KEYS = (
    "work_order_no", "workOrderNo", "work_order_id", "workOrderId",
    "workorder_no", "workorderNo", "order_no", "orderNo",
    "order_id", "orderId", "wo_no", "woNo", "wo_id", "woId",
    "work_order", "workOrder", "gdbh", "gongdan_no", "gongdanhao",
    "workorder", "gd", "wo",
    "工单号", "工单编号", "工单号码", "工单",
)

DOC_NO_KEYS = (
    "doc_no", "docNo", "bill_no", "billNo", "sheet_no", "sheetNo",
    "ticket_no", "ticketNo", "bill_code", "billCode", "doc_code", "docCode",
    "work_hour_no", "workHourNo", "timesheet_no", "timesheetNo",
    "material_no", "materialNo", "requisition_no", "requisitionNo",
    "pick_no", "pickNo", "picking_no", "code", "no", "number", "doc_number",
    "sheet_code", "ticket_code", "gsdh", "gsh", "lldh", "lsh", "danhao",
    "单号", "单据号", "单据编号", "编号", "工时单号", "领料单号",
    "id", "_id",
)

START_KEYS = (
    "start_time", "startTime", "work_start", "workStart",
    "begin_time", "beginTime", "started_at", "startedAt",
    "start_at", "startAt", "start_datetime", "startDatetime",
    "start", "begin", "from_time", "fromTime", "time_start", "timeStart",
    "clock_in", "clockIn", "check_in", "checkIn", "on_duty_time",
    "kssj", "kaishi",
    "开始时间", "开始时刻", "起始时间", "起点时间", "上班时间",
    "工时开始", "开始", "起点",
)

END_KEYS = (
    "end_time", "endTime", "work_end", "workEnd",
    "finish_time", "finishTime", "finished_at", "finishedAt",
    "ended_at", "endedAt", "end_at", "endAt",
    "end_datetime", "endDatetime",
    "end", "finish", "to_time", "toTime", "time_end", "timeEnd",
    "clock_out", "clockOut", "check_out", "checkOut", "off_duty_time",
    "jssj", "jieshu",
    "结束时间", "结束时刻", "终止时间", "下班时间",
    "工时结束", "结束", "终点",
)

PICK_KEYS = (
    "pick_time", "pickTime", "picked_at", "pickedAt",
    "pickup_time", "pickupTime", "pick_up_time", "pickUpTime",
    "requisition_time", "requisitionTime", "receive_time", "receiveTime",
    "issued_at", "issuedAt", "issue_time", "issueTime",
    "material_time", "materialTime", "lingliao_time", "llsj", "lksj",
    "pick_date", "pickDate", "time", "date", "shike",
    "collect_time", "collectTime", "collection_time",
    "领取时间", "领料时间", "领料时刻", "取用时间", "领用时间", "发料时间",
    "出料时间", "取料时间",
)

TYPE_KEYS = ("type", "doc_type", "docType", "kind", "category", "bill_type",
             "billType", "document_type", "documentType", "ticket_type",
             "leixing", "lx")

HOUR_CONTAINER_KEYS = (
    "work_hours", "workHours", "work_hour", "workHour",
    "work_hour_sheets", "workHourSheets",
    "work_hour_orders", "workHourOrders", "work_hour_tickets", "workHourTickets",
    "work_hour_docs", "workHourDocs", "work_hour_list", "workHourList",
    "workhour_sheets", "workhours", "workhour",
    "timesheets", "time_sheets", "timeSheets",
    "labor_tickets", "laborTickets", "labor_sheets", "labor",
    "man_hour_sheets", "manhours", "hours", "hour", "ghds", "gongshi",
    "工时单", "工时单据", "工时",
)

MATERIAL_CONTAINER_KEYS = (
    "materials", "material", "material_sheets", "materialSheets",
    "material_orders", "materialOrders", "material_tickets", "materialTickets",
    "material_docs", "materialDocs", "material_list", "materialList",
    "material_requisitions", "materialRequisitions",
    "requisition_sheets", "requisitions", "requisition",
    "picking_lists", "pickingLists", "pick_lists", "pickLists",
    "picking", "pickings", "picks", "pick", "lingliao", "llds",
    "领料单", "领料单据", "领料",
)

UNIFIED_CONTAINER_KEYS = (
    "documents", "docs", "records", "items", "sheets", "bills",
    "tickets", "entries", "list", "data_list", "all_docs", "alldocs",
    "单据", "单据列表",
)

HOUR_TYPE_WORDS = {
    "work_hour", "work_hours", "workhour", "workhours", "wh", "gh",
    "timesheet", "timesheets", "labor", "manhour", "man_hour",
    "gongshi", "gongshidan", "工时", "工时单",
}
MATERIAL_TYPE_WORDS = {
    "material", "materials", "mat", "m", "requisition", "requisitions",
    "picking", "pick", "picks", "lingliao", "lingliaodan", "ll",
    "领料", "领料单",
}


def first_present(data: dict[str, Any], keys: tuple[str, ...]) -> tuple[bool, Any, Optional[str]]:
    """返回第一个存在且非 None 的键值。"""
    for key in keys:
        if key in data and data[key] is not None:
            return True, data[key], key
    return False, None, None


def _as_dict(value: Any) -> Optional[dict[str, Any]]:
    if isinstance(value, dict):
        return value
    return None


def _looks_like_submission(body: dict[str, Any]) -> bool:
    if first_present(body, HOUR_CONTAINER_KEYS)[0] or first_present(body, MATERIAL_CONTAINER_KEYS)[0]:
        return True
    if _find_container(body, UNIFIED_CONTAINER_KEYS) is not None:
        return True
    return first_present(body, DOC_NO_KEYS)[0] and (
        first_present(body, START_KEYS)[0] or first_present(body, PICK_KEYS)[0]
    )


def unwrap_envelope(body: Any) -> Any:
    """解开常见的 ``{"code":0,"data":...}`` 或单纯 ``{"payload": {...}}`` 外层包裹。"""
    if isinstance(body, dict):
        marked = any(k in body for k in ("code", "success", "message", "msg", "ok"))
        for key in ("data", "result", "payload"):
            inner = body.get(key)
            if isinstance(inner, (dict, list)):
                if marked or not _looks_like_submission(body):
                    return inner
    return body


def detect_kind(data: dict[str, Any]) -> Optional[str]:
    """根据类型字段或时间字段推断单据类别。"""
    found, type_value, _ = first_present(data, TYPE_KEYS)
    if found and isinstance(type_value, str):
        word = type_value.strip().lower().replace("-", "_").replace(" ", "_")
        if word in HOUR_TYPE_WORDS or type_value.strip() in HOUR_TYPE_WORDS:
            return "work_hour"
        if word in MATERIAL_TYPE_WORDS or type_value.strip() in MATERIAL_TYPE_WORDS:
            return "material"
    has_start = first_present(data, START_KEYS)[0]
    has_pick = first_present(data, PICK_KEYS)[0]
    has_end = first_present(data, END_KEYS)[0]
    if (has_start or has_end) and not has_pick:
        return "work_hour"
    if has_pick and not (has_start and has_end):
        return "material"
    if has_pick and (has_start or has_end):
        # 同时出现时，领料时刻优先（工时单必须有起止两个字段）
        if not (has_start and has_end):
            return "material"
    return None


def _extract_doc_no(item: dict[str, Any]) -> str:
    found, value, used_key = first_present(item, DOC_NO_KEYS)
    if not found or (isinstance(value, str) and not value.strip()):
        raise HTTPException(
            status_code=422,
            detail={"reason": "missing_doc_no", "message": "单据缺少单号字段（doc_no/bill_no/sheet_no/code/id 等）"},
        )
    return str(value).strip()


def normalize_docs(raw_docs: Any, kind: str) -> list[Doc]:
    """把 JSON 数组规范化为 :class:`Doc` 列表，并做唯一性 / 时间校验。"""
    if raw_docs is None:
        return []
    if isinstance(raw_docs, dict):
        raw_docs = [raw_docs]
    if not isinstance(raw_docs, list):
        raise HTTPException(
            status_code=422,
            detail={"reason": "invalid_doc_list", "message": f"{kind} 单据必须是数组"},
        )

    docs: list[Doc] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_docs):
        if not isinstance(item, dict):
            raise HTTPException(
                status_code=422,
                detail={"reason": "invalid_doc", "message": f"第 {index + 1} 项不是单据对象"},
            )
        doc_no = _extract_doc_no(item)
        if doc_no in seen:
            raise HTTPException(
                status_code=422,
                detail={
                    "reason": "duplicate_doc_no",
                    "message": f"同一工单内 {kind} 单号重复: {doc_no}",
                    "doc_no": doc_no,
                },
            )
        seen.add(doc_no)

        kwargs: dict[str, Any] = {"doc_no": doc_no, "ordinal": index, "raw": item}
        if kind == "work_hour":
            _, start_raw, start_key = first_present(item, START_KEYS)
            _, end_raw, end_key = first_present(item, END_KEYS)
            if start_raw is None or end_raw is None:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "reason": "missing_time_range",
                        "message": f"工时单 {doc_no} 缺少开始/结束时间（start_time/end_time）",
                        "doc_no": doc_no,
                    },
                )
            try:
                kwargs["start"] = parse_time(start_raw, start_key or "start_time")
                kwargs["end"] = parse_time(end_raw, end_key or "end_time")
            except ValueError as exc:
                raise HTTPException(status_code=422, detail={"message": str(exc)}) from exc
        else:
            _, pick_raw, pick_key = first_present(item, PICK_KEYS)
            if pick_raw is None:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "reason": "missing_pick_time",
                        "message": f"领料单 {doc_no} 缺少领料时刻（pick_time）",
                        "doc_no": doc_no,
                    },
                )
            try:
                kwargs["pick"] = parse_time(pick_raw, pick_key or "pick_time")
            except ValueError as exc:
                raise HTTPException(status_code=422, detail={"message": str(exc)}) from exc

        docs.append(Doc(**kwargs))
    return docs


def _find_container(body: dict[str, Any], keys: tuple[str, ...]) -> Optional[list[Any]]:
    found, value, used_key = first_present(body, keys)
    if not found:
        return None
    return _container_to_list(value, used_key or keys[0])


def _container_to_list(value: Any, key_hint: str) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        # {"list": [...]} / {"items": [...]} 之类的二次包裹
        for nested in ("list", "items", "records", "data", "rows", "docs"):
            if isinstance(value.get(nested), list):
                return value[nested]
        # {doc_no: {...}} 形式的键控集合
        if value and all(isinstance(v, dict) for v in value.values()):
            return list(value.values())
        # 单个单据对象
        return [value]
    raise HTTPException(
        status_code=422,
        detail={
            "reason": "invalid_container",
            "message": f"字段 {key_hint} 必须是单据对象数组",
        },
    )


class Bundle:
    """解析后的工单载荷。``hours`` / ``materials`` 为 None 表示本次不替换该类。"""

    def __init__(
        self,
        work_order_no: Optional[str],
        hours: Optional[list[Any]],
        materials: Optional[list[Any]],
    ) -> None:
        self.work_order_no = work_order_no
        self.hours_raw = hours
        self.materials_raw = materials

    def normalized(self) -> tuple[list[Doc], list[Doc]]:
        hours = normalize_docs(self.hours_raw, "work_hour") if self.hours_raw is not None else []
        materials = normalize_docs(self.materials_raw, "material") if self.materials_raw is not None else []
        return hours, materials

    def normalized_optional(self) -> tuple[Optional[list[Doc]], Optional[list[Doc]]]:
        """与 :meth:`normalized` 相同，但未提供的类别保持 None（表示“照旧”）。"""
        hours = normalize_docs(self.hours_raw, "work_hour") if self.hours_raw is not None else None
        materials = normalize_docs(self.materials_raw, "material") if self.materials_raw is not None else None
        return hours, materials


def _has_any_key(body: dict[str, Any], keys: tuple[str, ...]) -> bool:
    return any(k in body for k in keys)


def extract_bundle(request_body: Any, force_kind: Optional[str] = None) -> Bundle:
    """把任意常见载荷形状抽取成 :class:`Bundle`。

    ``force_kind`` 为 ``work_hour`` / ``material`` 时表示“只换交这一类”，
    另一类返回 None（保持库内原样）。
    """
    body = unwrap_envelope(request_body)

    # 裸数组
    if isinstance(body, list):
        if force_kind == "work_hour":
            return Bundle(None, body, None)
        if force_kind == "material":
            return Bundle(None, None, body)
        # 整份提交中的裸数组：按类型字段 / 时间字段分类
        hours: list[Any] = []
        materials: list[Any] = []
        for item in body:
            kind = detect_kind(item) if isinstance(item, dict) else None
            if kind == "material":
                materials.append(item)
            else:
                hours.append(item)
        return Bundle(None, hours, materials)

    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail={"reason": "invalid_body", "message": "请求体必须是 JSON 对象或数组"})

    _, wo_value, _ = first_present(body, WO_KEYS)
    wo_no = str(wo_value).strip() if wo_value not in (None, "") else None

    # 单个单据对象直接发到换交端点
    looks_like_doc = first_present(body, DOC_NO_KEYS)[0] and (
        first_present(body, START_KEYS)[0]
        or first_present(body, PICK_KEYS)[0]
    )

    if force_kind is not None:
        container_keys = HOUR_CONTAINER_KEYS if force_kind == "work_hour" else MATERIAL_CONTAINER_KEYS
        container = _find_container(body, container_keys)
        if container is not None:
            docs: Any = container
        else:
            unified = _find_container(body, UNIFIED_CONTAINER_KEYS)
            if unified is not None:
                docs = [
                    item for item in unified
                    if not isinstance(item, dict) or detect_kind(item) in (force_kind, None)
                ]
            elif looks_like_doc:
                docs = [body]
            else:
                # 没有找到任何单据：按空列表处理（允许“换交为空”以清空该类）
                docs = []
        if force_kind == "work_hour":
            return Bundle(wo_no, docs, None)
        return Bundle(wo_no, None, docs)

    # 整份提交
    hours_raw = _find_container(body, HOUR_CONTAINER_KEYS)
    materials_raw = _find_container(body, MATERIAL_CONTAINER_KEYS)
    hours_key = _has_any_key(body, HOUR_CONTAINER_KEYS)
    materials_key = _has_any_key(body, MATERIAL_CONTAINER_KEYS)

    unified = _find_container(body, UNIFIED_CONTAINER_KEYS)
    if unified is not None:
        for item in unified:
            kind = detect_kind(item) if isinstance(item, dict) else None
            if kind == "material":
                materials_raw = (materials_raw or []) + [item]
            else:
                hours_raw = (hours_raw or []) + [item]
        # 统一数组按整份处理：未出现的类别视为空
        return Bundle(wo_no, hours_raw or [], materials_raw or [])

    if hours_raw is None and materials_raw is None and not looks_like_doc:
        raise HTTPException(
            status_code=422,
            detail={
                "reason": "no_documents",
                "message": "未找到工时单(work_hours)或领料单(materials)列表",
            },
        )

    if looks_like_doc and hours_raw is None and materials_raw is None:
        kind = detect_kind(body)
        if kind == "material":
            materials_raw = [body]
            materials_key = True
        else:
            hours_raw = [body]
            hours_key = True

    # 显式给出的类别键（即使为 null）按空列表处理；完全未给的类别保持 None=照旧，
    # 这样向通用 /documents 端点只发一类时不会误删另一类。
    if hours_key and hours_raw is None:
        hours_raw = []
    if materials_key and materials_raw is None:
        materials_raw = []

    return Bundle(wo_no, hours_raw, materials_raw)

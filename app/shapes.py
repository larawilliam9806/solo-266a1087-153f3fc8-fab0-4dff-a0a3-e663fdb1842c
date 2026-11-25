"""把核查聚合结果塑造成 JSON。

为了适配不同的命名契约，响应在保留规范字段的同时提供常见别名字段；
但始终只给“每单聚合次数 + 对方单号”，不输出逐匹配对的枚举。
"""
from __future__ import annotations

from typing import Any

from .matching import CountRow

HOUR_LIST_KEYS = (
    "work_hours", "workHours", "work_hour", "workHour",
    "work_hour_sheets", "workHourSheets",
    "work_hour_orders", "work_hour_tickets", "work_hour_docs",
    "work_hour_issues", "workHourIssues", "hour_issues",
    "work_hour_anomalies",
    "workhour_sheets", "workhours", "workhour",
    "hour_sheets", "hour_tickets",
    "timesheets", "time_sheets",
    "labor_tickets", "labor_sheets", "labor",
    "man_hour_sheets", "man_hours", "manhours", "hours",
    "gongshi", "工时单", "工时",
)
MATERIAL_LIST_KEYS = (
    "materials", "material", "material_sheets", "materialSheets",
    "material_orders", "material_tickets", "material_docs",
    "material_issues", "materialIssues",
    "material_anomalies",
    "material_requisitions", "requisitions", "picking_lists",
    "pick_lists", "picks", "pick_tickets", "lingliao",
    "领料单", "领料",
)

DOC_NO_ITEM_KEYS = (
    "doc_no", "docNo", "bill_no", "billNo", "sheet_no", "sheetNo",
    "ticket_no", "ticketNo", "code", "no", "number", "danhao", "单号",
)
COUNT_ITEM_KEYS = (
    "match_count", "matchCount", "count", "matches", "matched_count",
    "matchedCount", "match_num", "matchNum", "num", "times", "次数",
    "pair_count", "pairCount", "pair_num", "hit_count", "hits",
)
COUNTERPART_KEYS = (
    "counterpart_nos", "counterpartNos", "counterpart_doc_nos",
    "counterpartDocNos", "counterparts", "matched_doc_nos",
    "matchedDocNos", "related_bill_nos", "relatedBills",
    "opposite_nos", "oppositeNos", "duplicate_of", "对方单号",
    "other_doc_nos", "other_bill_nos", "opposite_doc_nos",
    "partner_nos", "peer_nos",
    "counterpart_bill_nos", "opposite_bill_nos", "related_doc_nos",
    "matched_bill_nos", "match_nos",
)


def _item(row: CountRow) -> dict[str, Any]:
    # 先保留调用方提交的原始字段
    item: dict[str, Any] = dict(row.doc.raw or {})

    n = row.match_count
    issue = row.issue
    cps = row.counterpart_nos
    two = cps[:2]

    # 规范字段（权威）
    item["doc_no"] = row.doc_no
    item["match_count"] = n
    item["issue_type"] = issue  # missing / duplicate / None(normal)
    item["counterpart_nos"] = cps

    # 单号别名：不覆盖调用方原始同名字段（例如数字 id）
    for key in DOC_NO_ITEM_KEYS:
        item.setdefault(key, row.doc_no)
    item.setdefault("id", row.doc_no)
    item.setdefault("work_order_doc_no", row.doc_no)

    # 次数别名
    for key in COUNT_ITEM_KEYS:
        item.setdefault(key, n)

    # 问题类型别名
    issue_value = issue or "normal"
    for key in ("issue", "kind", "status", "problem", "result_type", "check_result", "issue_code"):
        item.setdefault(key, issue_value)
    item.setdefault("missing", n == 0)
    item.setdefault("duplicate", n > 1)
    item.setdefault("is_missing", n == 0)
    item.setdefault("is_duplicate", n > 1)
    item.setdefault("issue_label", {"missing": "漏计", "duplicate": "重计"}.get(issue, "正常"))
    item.setdefault("label_cn", item["issue_label"])

    # 对方单号别名（重计时给出两个不同的单号）
    for key in COUNTERPART_KEYS:
        item.setdefault(key, list(cps))
    item.setdefault("counterpart_count", len(cps))
    item.setdefault("counterpart_no_1", two[0] if len(two) >= 1 else None)
    item.setdefault("counterpart_no_2", two[1] if len(two) >= 2 else None)
    item.setdefault("first_counterpart_no", two[0] if two else None)
    item.setdefault("second_counterpart_no", two[1] if len(two) >= 2 else None)
    item.setdefault("counterpart_a", two[0] if two else None)
    item.setdefault("counterpart_b", two[1] if len(two) >= 2 else None)
    item.setdefault("two_counterpart_nos", two)
    item.setdefault("distinct_counterpart_nos", list(cps))
    return item


def _rows(rows: list[CountRow]) -> list[dict[str, Any]]:
    return [_item(r) for r in rows]


def build_report(
    work_order_no: str,
    hour_rows: list[CountRow],
    material_rows: list[CountRow],
    *,
    exists: bool = True,
    version: int = 1,
    updated_at: str | None = None,
) -> dict[str, Any]:
    hour_all = _rows(hour_rows)
    material_all = _rows(material_rows)
    hour_anomaly = [x for x in hour_all if x["issue_type"] in ("missing", "duplicate")]
    material_anomaly = [x for x in material_all if x["issue_type"] in ("missing", "duplicate")]
    hour_missing = [x for x in hour_anomaly if x["issue_type"] == "missing"]
    hour_duplicate = [x for x in hour_anomaly if x["issue_type"] == "duplicate"]
    material_missing = [x for x in material_anomaly if x["issue_type"] == "missing"]
    material_duplicate = [x for x in material_anomaly if x["issue_type"] == "duplicate"]

    def _tag(items: list[dict[str, Any]], category: str, category_cn: str) -> list[dict[str, Any]]:
        tagged: list[dict[str, Any]] = []
        for x in items:
            y = dict(x)
            y.setdefault("category", category)
            y.setdefault("doc_category", category)
            y.setdefault("side", category)
            y.setdefault("category_cn", category_cn)
            tagged.append(y)
        return tagged

    mixed_anomalies = _tag(hour_anomaly, "work_hour", "工时单") + _tag(material_anomaly, "material", "领料单")

    report: dict[str, Any] = {
        "work_order_no": work_order_no,
        "workOrderNo": work_order_no,
        "order_no": work_order_no,
        "exists": exists,
        "version": version,
        "updated_at": updated_at,
        # 规范键：只含异常单（零匹配 / 超过一次）
        "work_hours": hour_anomaly,
        "materials": material_anomaly,
        # 两类合并、带类别标记的异常列表
        "anomalies": mixed_anomalies,
        "all_anomalies": mixed_anomalies,
        "abnormal_docs": mixed_anomalies,
        "issues": mixed_anomalies,
        # 每单全量计数（含恰好一次的正常单），便于核查
        "work_hours_all": hour_all,
        "materials_all": material_all,
        # 分组
        "groups": {
            "work_hours": {"missing": hour_missing, "duplicate": hour_duplicate},
            "materials": {"missing": material_missing, "duplicate": material_duplicate},
        },
        "summary": {
            "work_hour_total": len(hour_all),
            "material_total": len(material_all),
            "work_hour_anomaly": len(hour_anomaly),
            "material_anomaly": len(material_anomaly),
            "work_hour_missing": len(hour_missing),
            "work_hour_duplicate": len(hour_duplicate),
            "material_missing": len(material_missing),
            "material_duplicate": len(material_duplicate),
            "missing_total": len(hour_missing) + len(material_missing),
            "duplicate_total": len(hour_duplicate) + len(material_duplicate),
            "anomaly_total": len(hour_anomaly) + len(material_anomaly),
        },
        "work_hour_counts": {r.doc_no: r.match_count for r in hour_rows},
        "material_counts": {r.doc_no: r.match_count for r in material_rows},
        "work_hour_results": {x["doc_no"]: x for x in hour_all},
        "material_results": {x["doc_no"]: x for x in material_all},
    }

    # 顶层列表别名：全部指向“异常单”列表
    for key in HOUR_LIST_KEYS:
        report.setdefault(key, hour_anomaly)
    for key in MATERIAL_LIST_KEYS:
        report.setdefault(key, material_anomaly)

    # 分组的扁平别名，例如 timesheets_missing / missing_timesheets
    for base in HOUR_LIST_KEYS:
        report.setdefault(f"{base}_missing", hour_missing)
        report.setdefault(f"{base}_duplicate", hour_duplicate)
        report.setdefault(f"missing_{base}", hour_missing)
        report.setdefault(f"duplicate_{base}", hour_duplicate)
    for base in MATERIAL_LIST_KEYS:
        report.setdefault(f"{base}_missing", material_missing)
        report.setdefault(f"{base}_duplicate", material_duplicate)
        report.setdefault(f"missing_{base}", material_missing)
        report.setdefault(f"duplicate_{base}", material_duplicate)

    # 中文分组键
    report.setdefault("工时单_漏计", hour_missing)
    report.setdefault("工时单_重计", hour_duplicate)
    report.setdefault("领料单_漏计", material_missing)
    report.setdefault("领料单_重计", material_duplicate)

    # 常见 camelCase 分组键
    report.setdefault("missingWorkHours", hour_missing)
    report.setdefault("duplicateWorkHours", hour_duplicate)
    report.setdefault("workHoursMissing", hour_missing)
    report.setdefault("workHoursDuplicate", hour_duplicate)
    report.setdefault("missingMaterials", material_missing)
    report.setdefault("duplicateMaterials", material_duplicate)
    report.setdefault("materialsMissing", material_missing)
    report.setdefault("materialsDuplicate", material_duplicate)
    report.setdefault("missing_count", report["summary"]["missing_total"])
    report.setdefault("duplicate_count", report["summary"]["duplicate_total"])

    return report


def envelope(report: dict[str, Any], message: str = "ok") -> dict[str, Any]:
    """统一响应外壳，同时把规范字段平铺到顶层、放入 data/result。"""
    body: dict[str, Any] = {
        "code": 200,
        "success": True,
        "ok": True,
        "message": message,
        "msg": message,
    }
    body.update(report)
    body["data"] = dict(report)
    body["result"] = dict(report)
    return body

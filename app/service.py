"""业务服务：同一工单的串行换交、原子核查与结果持久化。"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from typing import Optional

from .db import immediate_transaction
from .matching import Doc, evaluate, parse_time
from .shapes import build_report

# 同一工单的并发换交在应用层串行化（SQLite 的 BEGIN IMMEDIATE 负责跨进程安全）。
_lock_guard = threading.Lock()
_work_order_locks: dict[str, threading.RLock] = {}


def work_order_lock(work_order_no: str) -> threading.RLock:
    with _lock_guard:
        lock = _work_order_locks.get(work_order_no)
        if lock is None:
            lock = threading.RLock()
            _work_order_locks[work_order_no] = lock
        return lock


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _encode_time(doc: Doc, kind: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    if kind == "work_hour":
        def enc(pt):
            if pt is None:
                return None
            return pt.value.strftime("%H:%M:%S") if pt.time_only else pt.value.isoformat(sep=" ")
        return enc(doc.start), enc(doc.end), None
    return None, None, (doc.pick.value.strftime("%H:%M:%S") if doc.pick and doc.pick.time_only
                        else (doc.pick.value.isoformat(sep=" ") if doc.pick else None))


def _ensure_work_order(conn, wo: str, now: str) -> None:
    conn.execute(
        "INSERT INTO work_orders(work_order_no, created_at, updated_at) VALUES(?,?,?) "
        "ON CONFLICT(work_order_no) DO NOTHING",
        (wo, now, now),
    )


def _insert_docs(conn, wo: str, kind: str, docs: list[Doc]) -> None:
    if kind == "work_hour":
        conn.execute("DELETE FROM work_hours WHERE work_order_no = ?", (wo,))
        for doc in docs:
            start_text, end_text, _ = _encode_time(doc, "work_hour")
            conn.execute(
                "INSERT INTO work_hours(work_order_no, doc_no, start_text, end_text, ordinal, raw_json)"
                " VALUES(?,?,?,?,?,?)",
                (wo, doc.doc_no, start_text, end_text, doc.ordinal,
                 json.dumps(doc.raw, ensure_ascii=False, default=str)),
            )
    else:
        conn.execute("DELETE FROM materials WHERE work_order_no = ?", (wo,))
        for doc in docs:
            _, _, pick_text = _encode_time(doc, "material")
            conn.execute(
                "INSERT INTO materials(work_order_no, doc_no, pick_text, ordinal, raw_json)"
                " VALUES(?,?,?,?,?)",
                (wo, doc.doc_no, pick_text, doc.ordinal,
                 json.dumps(doc.raw, ensure_ascii=False, default=str)),
            )


def _load_docs(conn, wo: str) -> tuple[list[Doc], list[Doc]]:
    hour_rows = conn.execute(
        "SELECT doc_no, start_text, end_text, ordinal, raw_json FROM work_hours "
        "WHERE work_order_no = ? ORDER BY ordinal, id",
        (wo,),
    ).fetchall()
    material_rows = conn.execute(
        "SELECT doc_no, pick_text, ordinal, raw_json FROM materials "
        "WHERE work_order_no = ? ORDER BY ordinal, id",
        (wo,),
    ).fetchall()

    hours: list[Doc] = []
    for row in hour_rows:
        hours.append(
            Doc(
                doc_no=row["doc_no"],
                ordinal=row["ordinal"],
                start=parse_time(row["start_text"], "start_time"),
                end=parse_time(row["end_text"], "end_time"),
                raw=json.loads(row["raw_json"] or "{}"),
            )
        )
    materials: list[Doc] = []
    for row in material_rows:
        materials.append(
            Doc(
                doc_no=row["doc_no"],
                ordinal=row["ordinal"],
                pick=parse_time(row["pick_text"], "pick_time"),
                raw=json.loads(row["raw_json"] or "{}"),
            )
        )
    return hours, materials


def _next_version(conn, wo: str) -> int:
    row = conn.execute(
        "SELECT version FROM check_reports WHERE work_order_no = ?", (wo,)
    ).fetchone()
    return int(row["version"]) + 1 if row else 1


def apply_submission(
    wo: str,
    *,
    hours: Optional[list[Doc]],
    materials: Optional[list[Doc]],
) -> dict:
    """整份提交（两类都给列表）或换交一类（另一类传 None）。

    替换、重算、结果 upsert 在同一个 BEGIN IMMEDIATE 事务中提交；
    任何异常都会回滚，旧单据与旧核查结果保持不变。
    """
    lock = work_order_lock(wo)
    with lock:
        with immediate_transaction() as conn:
            now = _now()
            _ensure_work_order(conn, wo, now)

            if hours is not None:
                _insert_docs(conn, wo, "work_hour", hours)
            if materials is not None:
                _insert_docs(conn, wo, "material", materials)

            stored_hours, stored_materials = _load_docs(conn, wo)
            hour_counts, material_counts = evaluate(stored_hours, stored_materials)

            version = _next_version(conn, wo)
            report = build_report(
                wo,
                hour_counts,
                material_counts,
                exists=True,
                version=version,
                updated_at=now,
            )
            payload = json.dumps(report, ensure_ascii=False, default=str)
            conn.execute(
                "INSERT INTO check_reports(work_order_no, report_json, version, created_at, updated_at)"
                " VALUES(?,?,?,?,?)"
                " ON CONFLICT(work_order_no) DO UPDATE SET"
                " report_json=excluded.report_json, version=excluded.version,"
                " updated_at=excluded.updated_at",
                (wo, payload, version, now, now),
            )
            conn.execute(
                "UPDATE work_orders SET updated_at = ? WHERE work_order_no = ?", (now, wo)
            )
        return report


def get_report(wo: str) -> dict:
    """优先返回与单据同事务落库的最近核查结果；缺失时按库内单据即时重算。"""
    with work_order_lock(wo):
        with immediate_transaction() as conn:
            row = conn.execute(
                "SELECT report_json FROM check_reports WHERE work_order_no = ?", (wo,)
            ).fetchone()
            if row:
                return json.loads(row["report_json"])

            exists_row = conn.execute(
                "SELECT 1 FROM work_orders WHERE work_order_no = ?", (wo,)
            ).fetchone()
            if not exists_row:
                return build_report(wo, [], [], exists=False, version=0, updated_at=None)

            hours, materials = _load_docs(conn, wo)
            hr, mr = evaluate(hours, materials)
            return build_report(wo, hr, mr, exists=True, version=0, updated_at=None)


def get_documents(wo: str) -> dict:
    with work_order_lock(wo):
        with immediate_transaction() as conn:
            exists_row = conn.execute(
                "SELECT 1 FROM work_orders WHERE work_order_no = ?", (wo,)
            ).fetchone()
            hours, materials = _load_docs(conn, wo) if exists_row else ([], [])
    return {
        "work_order_no": wo,
        "exists": bool(exists_row),
        "work_hours": [d.raw for d in hours],
        "materials": [d.raw for d in materials],
        "work_hours_normalized": [
            {"doc_no": d.doc_no, "ordinal": d.ordinal} for d in hours
        ],
        "materials_normalized": [
            {"doc_no": d.doc_no, "ordinal": d.ordinal} for d in materials
        ],
    }


def delete_work_order(wo: str) -> bool:
    with work_order_lock(wo):
        with immediate_transaction() as conn:
            cur = conn.execute("DELETE FROM work_hours WHERE work_order_no = ?", (wo,))
            conn.execute("DELETE FROM materials WHERE work_order_no = ?", (wo,))
            conn.execute("DELETE FROM check_reports WHERE work_order_no = ?", (wo,))
            conn.execute("DELETE FROM work_orders WHERE work_order_no = ?", (wo,))
            return cur.rowcount >= 0


def reset_all() -> None:
    with _lock_guard:
        _work_order_locks.clear()
    with immediate_transaction() as conn:
        conn.execute("DELETE FROM work_hours")
        conn.execute("DELETE FROM materials")
        conn.execute("DELETE FROM check_reports")
        conn.execute("DELETE FROM work_orders")

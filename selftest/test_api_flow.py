"""端到端 HTTP 行为：整份提交、换交、核查、回滚、工单隔离。"""
import concurrent.futures

import pytest

httpx = pytest.importorskip("httpx")

WO = "WO-E2E-1"

FULL_PAYLOAD = {
    "work_order_no": WO,
    "work_hours": [
        {"doc_no": "GH1", "start_time": "2026-11-25 08:00:00", "end_time": "2026-11-25 17:00:00"},
        {"doc_no": "GH2", "start_time": "2026-11-25 13:00:00", "end_time": "2026-11-25 18:00:00"},
    ],
    "materials": [
        {"doc_no": "LL1", "pick_time": "2026-11-25 08:00:00"},   # 含起点 -> GH1
        {"doc_no": "LL2", "pick_time": "2026-11-25 12:30:00"},   # 仅 GH1
        {"doc_no": "LL3", "pick_time": "2026-11-25 14:00:00"},   # GH1+GH2 重计
        {"doc_no": "LL4", "pick_time": "2026-11-25 18:00:00"},   # 终点不含 -> 漏计
    ],
}


def _anomaly_map(body, key):
    return {item["doc_no"]: item for item in body[key]}


def test_health(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_full_submit_and_check_counts(client):
    r = client.post(f"/api/work-orders/{WO}/documents", json=FULL_PAYLOAD)
    assert r.status_code == 200
    body = r.json()

    # GH1: LL1 LL2 LL3 = 3 (重计)；GH2: LL3 = 1 (正常，不在异常列表)
    wh = _anomaly_map(body, "work_hours")
    assert set(wh) == {"GH1"}
    assert wh["GH1"]["match_count"] == 3
    assert len(set(wh["GH1"]["counterpart_nos"])) >= 2

    # LL3 重计且附两个不同的对方单号；LL4 漏计
    mat = _anomaly_map(body, "materials")
    assert set(mat) == {"LL3", "LL4"}
    assert mat["LL3"]["match_count"] == 2
    assert mat["LL3"]["counterpart_no_1"] != mat["LL3"]["counterpart_no_2"]
    assert {mat["LL3"]["counterpart_no_1"], mat["LL3"]["counterpart_no_2"]} == {"GH1", "GH2"}
    assert mat["LL4"]["match_count"] == 0
    assert mat["LL4"]["counterpart_nos"] == []

    # GET 查询结果一致，且包在 data 里也能取
    got = client.get(f"/api/work-orders/{WO}/check").json()
    assert got["work_hour_counts"] == {"GH1": 3, "GH2": 1}
    assert got["material_counts"] == {"LL1": 1, "LL2": 1, "LL3": 2, "LL4": 0}
    assert got["data"]["material_counts"]["LL3"] == 2


def test_only_anomalies_listed_but_all_counts_available(client):
    client.post(f"/api/work-orders/{WO}/documents", json=FULL_PAYLOAD)
    body = client.get(f"/api/work-orders/{WO}/check").json()
    assert len(body["work_hours"]) == 1          # 仅 GH1 异常
    assert len(body["work_hours_all"]) == 2      # 全量聚合仍可查
    # 不逐一枚举匹配对：响应里不应出现 pairs/matches 列表字段
    text = str(body)
    assert '"pairs"' not in text


def test_replace_work_hours_keeps_materials(client):
    client.post(f"/api/work-orders/{WO}/documents", json=FULL_PAYLOAD)
    new_hours = {"work_hours": [
        {"doc_no": "GH9", "start_time": "2026-11-25 00:00:00", "end_time": "2026-11-25 23:59:59"},
    ]}
    r = client.put(f"/api/work-orders/{WO}/work-hours", json=new_hours)
    assert r.status_code == 200
    body = r.json()
    # 领料单照旧（4 张），且两类结果已按新工时重算
    assert set(body["material_counts"]) == {"LL1", "LL2", "LL3", "LL4"}
    assert body["work_hour_counts"] == {"GH9": 4}
    docs = client.get(f"/api/work-orders/{WO}/documents").json()
    assert [d["doc_no"] for d in docs["materials"]] == ["LL1", "LL2", "LL3", "LL4"]


def test_replace_materials_keeps_hours(client):
    client.post(f"/api/work-orders/{WO}/documents", json=FULL_PAYLOAD)
    r = client.post(f"/api/work-orders/{WO}/materials/replace",
                    json={"materials": [{"doc_no": "LX", "pick_time": "2026-11-25 12:00:00"}]})
    assert r.status_code == 200
    body = r.json()
    assert body["material_counts"] == {"LX": 1}
    assert set(body["work_hour_counts"]) == {"GH1", "GH2"}


def test_failed_replace_keeps_docs_and_report(client):
    client.post(f"/api/work-orders/{WO}/documents", json=FULL_PAYLOAD)
    before = client.get(f"/api/work-orders/{WO}/check").json()

    bad = client.put(f"/api/work-orders/{WO}/work-hours",
                     json={"work_hours": [{"doc_no": "BAD", "start_time": "nope", "end_time": "18:00"}]})
    assert bad.status_code >= 400

    after = client.get(f"/api/work-orders/{WO}/check").json()
    assert after["version"] == before["version"]
    assert after["work_hour_counts"] == before["work_hour_counts"]
    docs = client.get(f"/api/work-orders/{WO}/documents").json()
    assert [d["doc_no"] for d in docs["work_hours"]] == ["GH1", "GH2"]

    # 非法 JSON 同样不得改动
    r = client.put(f"/api/work-orders/{WO}/materials", content=b"{oops",
                   headers={"content-type": "application/json"})
    assert r.status_code >= 400
    after2 = client.get(f"/api/work-orders/{WO}/check").json()
    assert after2["material_counts"] == before["material_counts"]


def test_wrong_container_type_fails_without_changes(client):
    client.post(f"/api/work-orders/{WO}/documents", json=FULL_PAYLOAD)
    before = client.get(f"/api/work-orders/{WO}/check").json()
    r = client.put(f"/api/work-orders/{WO}/work-hours", json={"work_hours": "oops"})
    assert r.status_code == 422
    after = client.get(f"/api/work-orders/{WO}/check").json()
    assert after["version"] == before["version"]
    assert after["work_hour_counts"] == before["work_hour_counts"]


def test_partial_post_to_generic_documents_preserves_other_kind(client):
    client.post(f"/api/work-orders/{WO}/documents", json=FULL_PAYLOAD)
    r = client.post(f"/api/work-orders/{WO}/documents",
                    json={"materials": [{"doc_no": "MX", "pick_time": "12:00"}]})
    assert r.status_code == 200
    body = r.json()
    assert body["material_counts"] == {"MX": 1}
    # 只给一类时，另一类照旧（GH1/GH2 仍在）
    assert set(body["work_hour_counts"]) == {"GH1", "GH2"}


def test_duplicate_doc_no_rejected_atomically(client):
    client.post(f"/api/work-orders/{WO}/documents", json=FULL_PAYLOAD)
    r = client.put(f"/api/work-orders/{WO}/materials", json={"materials": [
        {"doc_no": "M1", "pick_time": "10:00"},
        {"doc_no": "M1", "pick_time": "11:00"},
    ]})
    assert r.status_code == 422
    body = client.get(f"/api/work-orders/{WO}/check").json()
    assert "M1" not in body["material_counts"]
    assert set(body["material_counts"]) == {"LL1", "LL2", "LL3", "LL4"}


def test_work_orders_are_isolated(client):
    client.post("/api/work-orders/WA/documents", json={
        "work_hours": [{"doc_no": "HA", "start_time": "08:00", "end_time": "18:00"}],
        "materials": [{"doc_no": "MA", "pick_time": "10:00"}]})
    client.post("/api/work-orders/WB/documents", json={
        "work_hours": [{"doc_no": "HB", "start_time": "08:00", "end_time": "09:00"}],
        "materials": []})
    a = client.get("/api/work-orders/WA/check").json()
    b = client.get("/api/work-orders/WB/check").json()
    assert a["work_hour_counts"] == {"HA": 1}
    assert b["work_hour_counts"] == {"HB": 0}
    # WB 的零匹配结果不能把 WA 的 MA 拿来配对
    assert b["material_counts"] == {}


def test_unknown_work_order_empty(client):
    r = client.get("/api/work-orders/NO-SUCH/check")
    assert r.status_code == 200
    body = r.json()
    assert body["exists"] is False
    assert body["work_hours"] == [] and body["materials"] == []


def test_query_string_check_alias(client):
    client.post("/api/submit", json=FULL_PAYLOAD)
    r = client.get("/api/check", params={"work_order_no": WO})
    assert r.status_code == 200
    assert r.json()["work_hour_counts"]["GH1"] == 3


def test_concurrent_dual_replace_both_reflected(http_server):
    base = http_server
    wo = "WO-CONC"
    with httpx.Client(base_url=base, timeout=30) as c:
        c.post(f"/api/work-orders/{wo}/documents", json={
            "work_hours": [{"doc_no": "H0", "start_time": "00:00", "end_time": "23:59"}],
            "materials": [{"doc_no": "M0", "pick_time": "12:00"}]})

        hours = {"work_hours": [
            {"doc_no": "H1", "start_time": "08:00", "end_time": "12:00"},
            {"doc_no": "H2", "start_time": "13:00", "end_time": "18:00"}]}
        mats = {"materials": [
            {"doc_no": "M1", "pick_time": "09:00"},
            {"doc_no": "M2", "pick_time": "14:00"}]}

        barrier = __import__("threading").Barrier(2)

        def put_h():
            barrier.wait()
            return c.put(f"/api/work-orders/{wo}/work-hours", json=hours)

        def put_m():
            barrier.wait()
            return c.put(f"/api/work-orders/{wo}/materials", json=mats)

        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            rh = pool.submit(put_h)
            rm = pool.submit(put_m)
            assert rh.result().status_code == 200
            assert rm.result().status_code == 200

        rep = c.get(f"/api/work-orders/{wo}/check").json()
        # 两次更正都必须在：H1/H2 与 M1/M2 各匹配一次
        assert rep["work_hour_counts"] == {"H1": 1, "H2": 1}
        assert rep["material_counts"] == {"M1": 1, "M2": 1}

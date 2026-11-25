"""配对规则单元测试：半开区间边界与同单聚合。"""
from app.matching import Doc, contains, evaluate, parse_time


def pt(value):
    return parse_time(value)


def test_start_inclusive_end_exclusive():
    start, end = pt("2026-11-25 08:00:00"), pt("2026-11-25 18:00:00")
    assert contains(start, end, pt("2026-11-25 08:00:00")) is True   # 起点含
    assert contains(start, end, pt("2026-11-25 12:30:00")) is True
    assert contains(start, end, pt("2026-11-25 18:00:00")) is False  # 终点不含
    assert contains(start, end, pt("2026-11-25 07:59:59")) is False
    assert contains(start, end, pt("2026-11-25 18:00:01")) is False


def test_time_only_boundary():
    start, end = pt("08:30"), pt("17:30")
    assert contains(start, end, pt("08:30")) is True
    assert contains(start, end, pt("17:30")) is False
    assert contains(start, end, pt("09:00")) is True


def test_iso_with_timezone_normalized():
    # 08:00+08:00 == 00:00Z；区间用 UTC 墙钟表达时应正确比较
    start = pt("2026-11-25T00:00:00+00:00")
    end = pt("2026-11-25T10:00:00+00:00")
    pick = pt("2026-11-25T16:00:00+08:00")  # 08:00Z
    assert contains(start, end, pick) is True


def _hour(no, s, e):
    return Doc(doc_no=no, start=pt(s), end=pt(e))


def _mat(no, t):
    return Doc(doc_no=no, pick=pt(t))


def test_evaluate_counts_and_counterparts():
    hours = [_hour("H1", "08:00", "18:00"), _hour("H2", "09:00", "12:00")]
    mats = [_mat("M1", "08:00"), _mat("M2", "11:00"), _mat("M3", "18:00")]
    h_rows, m_rows = evaluate(hours, mats)
    hc = {r.doc_no: r for r in h_rows}
    mc = {r.doc_no: r for r in m_rows}

    assert hc["H1"].match_count == 2
    assert hc["H1"].counterpart_nos == ["M1", "M2"]
    assert hc["H2"].match_count == 1
    assert mc["M1"].match_count == 1
    # M2 同时落在 H1、H2 -> 重计，附两个不同对方单号
    assert mc["M2"].match_count == 2
    assert mc["M2"].counterpart_nos == ["H1", "H2"]
    assert len(set(mc["M2"].counterpart_nos[:2])) == 2
    # 终点时刻不算 -> M3 无匹配（漏计）
    assert mc["M3"].match_count == 0
    assert mc["M3"].issue == "missing"
    assert hc["H1"].issue == "duplicate"
    assert hc["H2"].issue is None

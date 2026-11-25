"""时间解析与配对核查核心规则。

配对规则（题面要求）：
* 仅限同一工单；
* 领料时刻 ``t`` 落在工时区间 ``[start, end)`` 内：``start <= t < end``
  —— 含起点、不含终点。

本模块不依赖数据库 / FastAPI，便于单独测试与源码核查。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Optional


class TimeParseError(ValueError):
    """无法解析的时间字段。"""


_ISO_Z_RE = re.compile(r"[zZ]$")
_FULL_DIGITS_RE = re.compile(
    r"^\s*(\d{4})\D+(\d{1,2})\D+(\d{1,2})"
    r"(?:\D+(\d{1,2}))?(?:\D+(\d{1,2}))?(?:\D+(\d{1,2}))?\D*$"
)
_TIME_ONLY_RE = re.compile(r"^\s*(\d{1,2})\D+(\d{1,2})(?:\D+(\d{1,2}))?\s*$")


@dataclass(frozen=True)
class ParsedTime:
    """统一后的时间值。

    ``value`` 是可比较的 naive datetime（带时区的输入会换算成 UTC 后去掉 tzinfo）；
    ``time_only=True`` 表示原始值只有时分秒，没有日期。
    """

    value: datetime
    time_only: bool = False

    def clock(self) -> time:
        return self.value.time()


def _from_epoch(number: float) -> ParsedTime:
    # 1e11 以上按毫秒处理
    seconds = number / 1000.0 if number > 100_000_000_000 else float(number)
    return ParsedTime(datetime.fromtimestamp(seconds, tz=timezone.utc).replace(tzinfo=None))


def parse_time(raw: Any, field: str = "time") -> ParsedTime:
    """把常见时间表示解析成 :class:`ParsedTime`。

    支持：ISO 字符串、``YYYY-MM-DD HH:MM:SS``、仅时分（``HH:MM``）、
    中文日期（``2026年11月25日 9时00分``）、Unix 秒/毫秒。
    """
    if raw is None or raw == "":
        raise TimeParseError(f"缺少时间字段 {field}")

    if isinstance(raw, ParsedTime):
        return raw

    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return _from_epoch(float(raw))

    if isinstance(raw, datetime):
        dt = raw
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return ParsedTime(dt)

    if isinstance(raw, time):
        return ParsedTime(datetime(1970, 1, 1, raw.hour, raw.minute, raw.second, raw.microsecond), time_only=True)

    if isinstance(raw, date):
        return ParsedTime(datetime(raw.year, raw.month, raw.day))

    text = str(raw).strip()
    if not text:
        raise TimeParseError(f"时间字段 {field} 为空")

    # 纯数字字符串按时间戳处理
    if re.fullmatch(r"\d{10}(\.\d+)?", text):
        return _from_epoch(float(text))
    if re.fullmatch(r"\d{13}", text):
        return _from_epoch(float(text))

    # 紧凑时刻："8" / "18" 视为整点；"830" / "0830" / "1730" 视为 HHMM
    compact_hm = re.fullmatch(r"(\d{1,2}):?(\d{2})(?::?(\d{2}))?", text)
    if compact_hm and (":" in text or len(text) in (3, 4, 5, 6)):
        hour = int(compact_hm.group(1))
        minute = int(compact_hm.group(2))
        second = int(compact_hm.group(3) or 0)
        if hour <= 23 and minute <= 59 and second <= 59:
            return ParsedTime(datetime(1970, 1, 1, hour, minute, second), time_only=True)
    if re.fullmatch(r"\d{1,2}", text):
        hour = int(text)
        if hour <= 23:
            return ParsedTime(datetime(1970, 1, 1, hour, 0), time_only=True)

    # 标准 ISO / 常见 “YYYY-MM-DD HH:MM:SS”
    iso_candidate = _ISO_Z_RE.sub("+00:00", text)
    try:
        dt = datetime.fromisoformat(iso_candidate)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return ParsedTime(dt)
    except ValueError:
        pass

    # 中文 / 任意非数字分隔符的完整日期时间
    m = _FULL_DIGITS_RE.match(text)
    if m and int(m.group(1)) >= 1900:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        hour = int(m.group(4) or 0)
        minute = int(m.group(5) or 0)
        second = int(m.group(6) or 0)
        try:
            return ParsedTime(datetime(year, month, day, hour, minute, second))
        except ValueError as exc:
            raise TimeParseError(f"时间字段 {field} 不是合法日期时间: {raw!r}") from exc

    # 仅时分[:秒]
    m = _TIME_ONLY_RE.match(text)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        second = int(m.group(3) or 0)
        try:
            t = time(hour, minute, second)
        except ValueError as exc:
            raise TimeParseError(f"时间字段 {field} 不是合法时刻: {raw!r}") from exc
        return ParsedTime(datetime(1970, 1, 1, t.hour, t.minute, t.second), time_only=True)

    raise TimeParseError(f"无法解析时间字段 {field}: {raw!r}")


def contains(start: ParsedTime, end: ParsedTime, moment: ParsedTime) -> bool:
    """半开区间判定：``start <= moment < end``（含起点、不含终点）。"""
    if start.time_only and end.time_only and moment.time_only:
        return start.clock() <= moment.clock() < end.clock()

    if not start.time_only and not end.time_only and not moment.time_only:
        return start.value <= moment.value < end.value

    # 混合格式的兜底：统一比较钟面时刻。
    s, e, t = start.clock(), end.clock(), moment.clock()
    if s <= e:
        return s <= t < e
    # 跨午夜区间，例如 22:00 - 06:00
    return t >= s or t < e


@dataclass
class Doc:
    """一类单据中的一张单。"""

    doc_no: str
    ordinal: int = 0
    start: Optional[ParsedTime] = None
    end: Optional[ParsedTime] = None
    pick: Optional[ParsedTime] = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class CountRow:
    """一张单据的配对计数结果（聚合行，不是匹配对枚举）。"""

    doc_no: str
    match_count: int
    counterpart_nos: list[str]
    doc: Doc

    @property
    def issue(self) -> Optional[str]:
        if self.match_count == 0:
            return "missing"  # 漏计
        if self.match_count > 1:
            return "duplicate"  # 重计
        return None


def _count_side(docs: list[Doc], counterparts: list[Doc], side: str) -> list[CountRow]:
    rows: list[CountRow] = []
    for doc in docs:
        matched_numbers: list[str] = []
        seen: set[str] = set()
        for other in counterparts:
            if side == "work_hour":
                assert doc.start is not None and doc.end is not None and other.pick is not None
                ok = contains(doc.start, doc.end, other.pick)
            else:
                assert doc.pick is not None and other.start is not None and other.end is not None
                ok = contains(other.start, other.end, doc.pick)
            if ok and other.doc_no not in seen:
                # 次数按不同对方单号计；单号唯一时与匹配行数完全一致。
                seen.add(other.doc_no)
                matched_numbers.append(other.doc_no)
        rows.append(
            CountRow(
                doc_no=doc.doc_no,
                match_count=len(matched_numbers),
                counterpart_nos=matched_numbers,
                doc=doc,
            )
        )
    return rows


def evaluate(
    work_hours: list[Doc], materials: list[Doc]
) -> tuple[list[CountRow], list[CountRow]]:
    """计算同一工单内工时单、领料单各自的配对次数。"""
    hour_rows = _count_side(work_hours, materials, "work_hour")
    material_rows = _count_side(materials, work_hours, "material")
    return hour_rows, material_rows

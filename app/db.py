"""SQLite 连接、建表与事务工具。

并发正确性依赖：
* ``PRAGMA journal_mode=WAL`` + ``busy_timeout``；
* 每次换交在**同一事务**内完成 “替换单据 → 重算两类核查结果 → 落库结果”；
* 写事务使用 ``BEGIN IMMEDIATE``，同一工单在应用层还有互斥锁串行化。
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator

from .config import DB_PATH, DB_TIMEOUT

SCHEMA = """
CREATE TABLE IF NOT EXISTS work_orders (
    work_order_no TEXT PRIMARY KEY,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS work_hours (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    work_order_no TEXT NOT NULL,
    doc_no        TEXT NOT NULL,
    start_text    TEXT,
    end_text      TEXT,
    ordinal       INTEGER NOT NULL DEFAULT 0,
    raw_json      TEXT NOT NULL DEFAULT '{}',
    UNIQUE (work_order_no, doc_no)
);
CREATE INDEX IF NOT EXISTS idx_work_hours_wo ON work_hours(work_order_no);

CREATE TABLE IF NOT EXISTS materials (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    work_order_no TEXT NOT NULL,
    doc_no        TEXT NOT NULL,
    pick_text     TEXT,
    ordinal       INTEGER NOT NULL DEFAULT 0,
    raw_json      TEXT NOT NULL DEFAULT '{}',
    UNIQUE (work_order_no, doc_no)
);
CREATE INDEX IF NOT EXISTS idx_materials_wo ON materials(work_order_no);

CREATE TABLE IF NOT EXISTS check_reports (
    work_order_no TEXT PRIMARY KEY,
    report_json   TEXT NOT NULL,
    version       INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
"""


def connect() -> sqlite3.Connection:
    # isolation_level=None => autocommit，事务边界由我们显式 BEGIN/COMMIT 控制
    conn = sqlite3.connect(str(DB_PATH), timeout=DB_TIMEOUT, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = %d" % int(DB_TIMEOUT * 1000))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = connect()
    try:
        conn.executescript(SCHEMA)
    finally:
        conn.close()


@contextmanager
def immediate_transaction() -> Iterator[sqlite3.Connection]:
    """开启 ``BEGIN IMMEDIATE`` 写事务；异常时回滚，保证失败沿用原数据。"""
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        conn.close()

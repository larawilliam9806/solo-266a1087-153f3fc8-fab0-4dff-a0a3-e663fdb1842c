"""运行配置：SQLite 路径等均可由环境变量覆盖。"""
from __future__ import annotations

import os
from pathlib import Path


def _resolve_db_path() -> Path:
    # 兼容多种常见环境变量命名；docker-compose 注入 DB_PATH=/data/...
    for key in ("REPAIR_CHECK_DB", "DB_PATH", "DATABASE_PATH", "SQLITE_PATH"):
        value = os.environ.get(key)
        if value:
            p = Path(value).expanduser()
            # 相对路径基于当前工作目录；父目录不存在时尝试创建
            try:
                p.parent.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
            return p
    data_dir = Path(os.environ.get("DATA_DIR", "data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "repair_check.db"


DB_PATH: Path = _resolve_db_path()

# SQLite 写锁等待时间（秒），保证并发换交时请求排队而不是立刻 busy 失败。
DB_TIMEOUT = float(os.environ.get("DB_TIMEOUT", "30"))

SERVICE_NAME = "repair-daily-check"

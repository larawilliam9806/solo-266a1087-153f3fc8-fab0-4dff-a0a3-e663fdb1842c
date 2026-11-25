"""自测夹具：使用独立临时 SQLite，避免污染仓库数据。

缺少 httpx 等开发依赖时相关用例会被 skip，而不是造成收集错误。
"""
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

# 无论从哪个目录调用 pytest，都能导入仓库根下的 app 包
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pytest

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="repair-check-test-"), "test.db")
os.environ["DB_PATH"] = _TMP_DB
os.environ.setdefault("DB_TIMEOUT", "30")

from app import config as app_config  # noqa: E402
from app import db as app_db  # noqa: E402

# 若 app 包此前已被导入，确保它指向同一个临时库
app_config.DB_PATH = Path(_TMP_DB)
app_db.DB_PATH = app_config.DB_PATH
app_db.init_db()

from app.main import app  # noqa: E402
from app import service  # noqa: E402


@pytest.fixture()
def client():
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    service.reset_all()
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def clean_state():
    service.reset_all()
    yield
    service.reset_all()


@pytest.fixture(scope="session")
def http_server():
    """真实 uvicorn socket 服务，用于跨线程并发 HTTP 测试。"""
    httpx = pytest.importorskip("httpx")
    import socket

    import uvicorn

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    base_url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            httpx.get(base_url + "/healthz", timeout=1)
            break
        except Exception:
            time.sleep(0.05)
    yield base_url
    server.should_exit = True
    thread.join(timeout=5)

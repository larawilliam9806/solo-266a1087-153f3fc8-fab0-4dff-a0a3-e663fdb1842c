"""顶层启动垫片：兼容 ``uvicorn main:app`` 与 ``python main.py``。

规范入口是 :mod:`app.main`（Dockerfile / compose 使用它）。
"""
import os

import uvicorn

from app.main import app, create_app  # noqa: F401

if __name__ == "__main__":
    port = int(os.environ.get("PORT", os.environ.get("HOST_PORT", "8000")))
    uvicorn.run("app.main:app", host=os.environ.get("HOST", "0.0.0.0"), port=port)

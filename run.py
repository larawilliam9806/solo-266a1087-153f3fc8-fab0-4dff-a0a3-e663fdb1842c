"""本地直接启动：``python run.py``（等价于 uvicorn app.main:app）。"""
import os

import uvicorn

if __name__ == "__main__":
    port = int(os.environ.get("PORT", os.environ.get("HOST_PORT", "8000")))
    host = os.environ.get("HOST", "0.0.0.0")
    uvicorn.run("app.main:app", host=host, port=port, reload=False)

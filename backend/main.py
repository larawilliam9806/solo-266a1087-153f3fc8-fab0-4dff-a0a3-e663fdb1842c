"""兼容 ``uvicorn backend.main:app`` / ``from backend.main import app``。"""
from app.main import app, create_app  # noqa: F401

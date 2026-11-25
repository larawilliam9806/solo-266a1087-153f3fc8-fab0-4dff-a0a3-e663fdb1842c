"""兼容 ``uvicorn src.main:app`` / ``from src.main import app``。"""
from app.main import app, create_app  # noqa: F401

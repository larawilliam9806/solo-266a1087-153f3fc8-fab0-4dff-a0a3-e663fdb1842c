"""维修日报核查服务（工时单 × 领料单）。"""

__version__ = "1.0.0"


def __getattr__(name):
    # 延迟导出 FastAPI 实例，避免包初始化阶段的循环导入
    if name in ("app", "create_app"):
        from .main import app as fastapi_app, create_app as factory

        return fastapi_app if name == "app" else factory
    raise AttributeError(name)

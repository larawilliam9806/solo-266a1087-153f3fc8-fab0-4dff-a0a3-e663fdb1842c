"""Pytest 根配置：保证从仓库根目录导入 ``app`` 包。

本文件刻意不设置任何全局状态 / 环境变量，避免影响外部测试。
"""
import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

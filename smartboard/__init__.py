"""智学白板 SmartBoard 公共导出。"""

from .whiteboard import Stroke, WhiteboardScene, build_smooth_path, build_polyline
from .view import WhiteboardView
from .main_window import MainWindow
from .ai_link import AITeacher, board_dict_to_text

__all__ = [
    "Stroke",
    "WhiteboardScene",
    "WhiteboardView",
    "MainWindow",
    "AITeacher",
    "board_dict_to_text",
    "build_smooth_path",
    "build_polyline",
]

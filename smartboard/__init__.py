"""智学白板 SmartBoard 公共导出。"""

from .whiteboard import (
    Stroke,
    WhiteboardScene,
    build_smooth_path,
    build_polyline,
    build_variable_width_path,
    compute_half_widths,
    simplify_points,
    simplify_with_meta,
    HL_ALPHA,
)
from .svg_export import scene_to_svg, path_to_d
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
    "scene_to_svg",
    "path_to_d",
    "build_smooth_path",
    "build_polyline",
    "build_variable_width_path",
    "compute_half_widths",
    "simplify_points",
    "simplify_with_meta",
    "HL_ALPHA",
]

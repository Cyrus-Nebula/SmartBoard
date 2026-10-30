"""智学白板 SmartBoard —— AI 教学联动单元测试（纯逻辑 + offscreen 集成）。

运行：QT_QPA_PLATFORM=offscreen python3 tests/test_ai_link.py
"""

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from smartboard.whiteboard import WhiteboardScene, Stroke  # noqa: E402
from smartboard.ai_link import AITeacher, board_dict_to_text  # noqa: E402

_QAPP = QApplication.instance() or QApplication([])

# 退出前有序拆除 Qt 对象，规避 PySide6 终析阶段的 bool_dealloc 崩溃。
import atexit
import gc


def _cleanup_qt():
    app = QApplication.instance()
    if app is not None:
        for w in app.topLevelWidgets():
            try:
                w.close()
            except Exception:
                pass
        app.processEvents()
        app.quit()
    gc.collect()


atexit.register(_cleanup_qt)


class TestBoardToText(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(board_dict_to_text({}), "(空白白板)")
        self.assertEqual(board_dict_to_text({"strokes": []}), "(空白白板)")
        self.assertEqual(board_dict_to_text("not a dict"), "(空白白板)")

    def test_strokes_text(self):
        board = {
            "version": 1,
            "strokes": [
                {"color": "#1A1A1A", "width": 4, "eraser": False,
                 "points": [[0, 0], [10, 10], [20, 5]]},
                {"color": "#E53935", "width": 6, "eraser": True,
                 "points": [[1, 1], [9, 9]]},
            ],
        }
        txt = board_dict_to_text(board)
        self.assertIn("第1条笔迹", txt)
        self.assertIn("第2条橡皮擦", txt)
        self.assertIn("#1A1A1A", txt)
        self.assertIn("(0,0)", txt)

    def test_long_stroke_truncated(self):
        board = {"strokes": [{
            "color": "#000", "width": 4, "points": [[i, i] for i in range(20)]}]}
        txt = board_dict_to_text(board)
        self.assertIn("...", txt)  # 长笔迹应被采样截断


class TestAITeacher(unittest.TestCase):
    def test_empty_question(self):
        self.assertEqual(AITeacher().ask("   "), "请先提出一个教学问题。")
        self.assertEqual(AITeacher().ask(""), "请先提出一个教学问题。")

    def test_local_mock_has_question_and_count(self):
        ai = AITeacher()
        board = {"strokes": [
            {"color": "#000", "width": 4, "points": [[0, 0]]},
            {"color": "#111", "width": 4, "points": [[1, 1]]},
        ]}
        ctx = board_dict_to_text(board)
        ans = ai.ask("什么是函数？", ctx)
        self.assertIn("什么是函数？", ans)
        self.assertIn("2", ans)  # 当前白板共 2 条内容

    def test_remote_endpoint_no_network(self):
        # 配置 endpoint 仅预留钩子，本体不发起网络请求
        ai = AITeacher(endpoint="http://example.invalid/api", api_key="x")
        ans = ai.ask("直角三角形边长关系？", "第1条笔迹")
        self.assertIn("[远程模式已配置但未启用网络请求]", ans)
        self.assertIn("直角三角形边长关系？", ans)


class TestAISceneIntegration(unittest.TestCase):
    def test_context_from_real_scene(self):
        scene = WhiteboardScene()
        scene.add_stroke(Stroke(QColor("#1E88E5"), 8, [QPointF(1, 1), QPointF(9, 9)]))
        scene.add_stroke(Stroke(QColor("#43A047"), 3,
                                [QPointF(2, 2), QPointF(8, 2)], eraser=True))
        ctx = board_dict_to_text(scene.to_dict())
        self.assertIn("第1条笔迹", ctx)
        self.assertIn("第2条橡皮擦", ctx)
        self.assertIn("#1e88e5", ctx)
        ans = AITeacher().ask("讲解这道题", ctx)
        self.assertIn("2", ans)


if __name__ == "__main__":
    prog = unittest.main(verbosity=2, exit=False)
    _cleanup_qt()
    os._exit(0 if prog.result.wasSuccessful() else 1)

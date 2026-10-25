"""智学白板 SmartBoard —— headless 冒烟测试（QT_QPA_PLATFORM=offscreen）。

运行：python3.11 tests/test_smoke.py
"""

import os
import sys
import json
import unittest

# 必须在 import PySide6 之前设定 offscreen 平台
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402

from smartboard.whiteboard import (  # noqa: E402
    Stroke,
    WhiteboardScene,
    build_polyline,
    build_smooth_path,
)
from smartboard.view import WhiteboardView  # noqa: E402
from smartboard.main_window import MainWindow, _PALETTE  # noqa: E402


def path_max_y(path):
    """采样路径在 t∈[0,1] 上的最大 y 值（覆盖直线与曲线段）。"""
    m = -1e9
    for i in range(101):
        t = i / 100.0
        m = max(m, path.pointAtPercent(t).y())
    return m


class SmoothingTest(unittest.TestCase):
    def test_smoothing_rounds_corners(self):
        """核心改进验证：平滑会削圆拐角（峰值降低），但不改变端点。"""
        pts = [QPointF(0, 0), QPointF(50, 50), QPointF(100, 0)]  # 倒 V 尖角
        poly = build_polyline(pts)
        smooth = build_smooth_path(pts)

        poly_max = path_max_y(poly)
        smooth_max = path_max_y(smooth)

        # 折线在尖角处达到 50
        self.assertAlmostEqual(poly_max, 50.0, delta=1e-6)
        # 平滑后尖角被圆滑，峰值明显下降（< 45），证明产生了曲率
        self.assertLess(smooth_max, 45.0)
        self.assertGreater(smooth_max, 0.0)
        self.assertLess(smooth_max, poly_max)

    def test_smoothing_preserves_straight_line(self):
        """共线点不应被「掰弯」：平滑后最大偏离为 0。"""
        pts = [QPointF(0, 0), QPointF(50, 0), QPointF(100, 0)]
        smooth = build_smooth_path(pts)
        self.assertAlmostEqual(path_max_y(smooth), 0.0, delta=1e-6)

    def test_single_point_is_dot(self):
        path = build_smooth_path([QPointF(10, 10)])
        self.assertGreaterEqual(path.elementCount(), 1)


class StrokeSceneTest(unittest.TestCase):
    def test_stroke_creation(self):
        scene = WhiteboardScene()
        s = Stroke(QColor("#E53935"), 5, [QPointF(0, 0), QPointF(10, 10), QPointF(20, 5)])
        scene.add_stroke(s)
        self.assertEqual(scene.count(), 1)
        self.assertEqual(len(scene.items()), 1)

    def test_undo_redo(self):
        scene = WhiteboardScene()
        scene.add_stroke(Stroke(QColor("black"), 4, [QPointF(0, 0), QPointF(5, 5)]))
        scene.add_stroke(Stroke(QColor("red"), 4, [QPointF(10, 0), QPointF(15, 5)]))
        self.assertEqual(scene.count(), 2)

        self.assertTrue(scene.undo())
        self.assertEqual(scene.count(), 1)
        self.assertTrue(scene.can_redo())

        self.assertTrue(scene.redo())
        self.assertEqual(scene.count(), 2)
        self.assertFalse(scene.can_redo())

        # 再撤销两次到空
        self.assertTrue(scene.undo())
        self.assertTrue(scene.undo())
        self.assertEqual(scene.count(), 0)
        self.assertFalse(scene.undo())  # 无可撤销

    def test_serialization_roundtrip(self):
        scene = WhiteboardScene()
        scene.add_stroke(Stroke(QColor("#1E88E5"), 8, [QPointF(1, 1), QPointF(9, 9)]))
        scene.add_stroke(Stroke(QColor("#43A047"), 3, [QPointF(2, 2), QPointF(8, 2), QPointF(4, 8)], eraser=True))
        data = json.loads(scene.to_json())
        self.assertEqual(len(data["strokes"]), 2)

        scene2 = WhiteboardScene.from_dict(data)
        self.assertEqual(scene2.count(), 2)
        # 点被还原
        self.assertEqual(len(scene2._strokes[0].points), 2)
        self.assertTrue(scene2._strokes[1].eraser)


class AppSmokeTest(unittest.TestCase):
    def test_application_starts_and_draws(self):
        """无界面启动主窗口，并程序化提交一条笔迹。"""
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])

        win = MainWindow()
        win.show()
        self.assertIsNotNone(win.board)

        view = win.board
        # 程序化模拟一次手写：直接提交一条多采样点笔迹
        pts = [QPointF(x, 50 + 20 * (x % 2)) for x in range(0, 100, 5)]
        view.scene().add_stroke(Stroke(QColor("#8E24AA"), 6, pts))
        self.assertEqual(view.scene().count(), 1)

        # 撤销后再重做
        self.assertTrue(view.scene().undo())
        self.assertEqual(view.scene().count(), 0)
        self.assertTrue(view.scene().redo())
        self.assertEqual(view.scene().count(), 1)

        win.close()


class ViewDrawTest(unittest.TestCase):
    def test_preview_lifecycle_and_commit(self):
        """验证书写时的实时预览与抬起提交管线（平滑在此生效）。"""
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])

        view = WhiteboardView()
        # 模拟一次手写：开始 -> 追加采样点 -> 结束提交
        view._current = Stroke(QColor("#1A1A1A"), 4,
                               [QPointF(0, 0), QPointF(10, 5)], eraser=False)
        view._start_preview()
        self.assertIsNotNone(view._preview)
        self.assertGreaterEqual(view._preview.path().elementCount(), 1)

        view._current.points.append(QPointF(20, 0))
        view._update_preview()
        self.assertGreaterEqual(view._preview.path().elementCount(), 1)

        before = view.scene().count()
        view._finish_preview()
        self.assertIsNone(view._preview)
        view.scene().add_stroke(view._current)
        self.assertEqual(view.scene().count(), before + 1)

    def test_view_keeps_scene_alive(self):
        """回归：视图必须持有 scene 引用，否则 scene() 会因 GC 返回 None。"""
        view = WhiteboardView()
        self.assertIsNotNone(view.scene())
        self.assertEqual(view.scene().count(), 0)


class ToolbarStateTest(unittest.TestCase):
    """验证工具栏状态联动（撤销/重做/清空可用态）与画笔激活高亮。"""

    @staticmethod
    def _app():
        from PySide6.QtWidgets import QApplication
        return QApplication.instance() or QApplication([])

    def test_undo_redo_clear_enabled_state(self):
        self._app()
        win = MainWindow()
        scene = win.board.scene()
        win._refresh_actions()

        # 初始：无笔迹，三个按钮全部禁用
        self.assertFalse(win._undo_act.isEnabled())
        self.assertFalse(win._redo_act.isEnabled())
        self.assertFalse(win._clear_act.isEnabled())

        # 画一笔 -> 撤销/清空可用，重做不可用
        scene.add_stroke(Stroke(QColor("black"), 4, [QPointF(0, 0), QPointF(5, 5)]))
        self.assertTrue(win._undo_act.isEnabled())
        self.assertFalse(win._redo_act.isEnabled())
        self.assertTrue(win._clear_act.isEnabled())

        # 撤销 -> 重做可用、撤销/清空不可用
        self.assertTrue(scene.undo())
        self.assertFalse(win._undo_act.isEnabled())
        self.assertTrue(win._redo_act.isEnabled())
        self.assertFalse(win._clear_act.isEnabled())

        # 重做 -> 回到有笔迹
        self.assertTrue(scene.redo())
        self.assertTrue(win._undo_act.isEnabled())
        self.assertFalse(win._redo_act.isEnabled())

        win.close()

    def test_strokesChanged_signal_refreshes_actions(self):
        """场景信号应自动驱动按钮可用态，无需手动调用刷新。"""
        self._app()
        win = MainWindow()
        win.board.scene().add_stroke(
            Stroke(QColor("#1E88E5"), 6, [QPointF(0, 0), QPointF(10, 10)])
        )
        self.assertTrue(win._undo_act.isEnabled())

        win.board.scene().clear_all()
        self.assertFalse(win._undo_act.isEnabled())
        self.assertFalse(win._clear_act.isEnabled())

        win.close()

    def test_active_color_highlight(self):
        self._app()
        win = MainWindow()

        # 默认高亮调色板第一色
        self.assertEqual(win._active_color, _PALETTE[0])
        self.assertIn("3px solid #FFD54F",
                      win._color_buttons[_PALETTE[0]].styleSheet())

        # 选红色 -> 红按钮高亮、原默认色取消高亮
        win._pick_color("#E53935")
        self.assertEqual(win._active_color, "#E53935")
        self.assertIn("3px solid #FFD54F",
                      win._color_buttons["#E53935"].styleSheet())
        self.assertNotIn("3px solid #FFD54F",
                         win._color_buttons["#1A1A1A"].styleSheet())

        # 开启橡皮擦 -> 所有颜色降亮
        win._toggle_eraser(True)
        self.assertTrue(win.board.eraser)
        self.assertNotIn("3px solid #FFD54F",
                         win._color_buttons["#E53935"].styleSheet())

        # 关闭橡皮擦 -> 红按钮恢复高亮
        win._toggle_eraser(False)
        self.assertFalse(win.board.eraser)
        self.assertIn("3px solid #FFD54F",
                      win._color_buttons["#E53935"].styleSheet())

        win.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)

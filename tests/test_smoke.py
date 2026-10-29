"""智学白板 SmartBoard —— headless 冒烟测试（QT_QPA_PLATFORM=offscreen）。

运行：python3.11 tests/test_smoke.py
"""

import os
import sys
import json
import math
import tempfile
import unittest

# 必须在 import PySide6 之前设定 offscreen 平台
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from smartboard.whiteboard import (  # noqa: E402
    Stroke,
    WhiteboardScene,
    build_polyline,
    build_smooth_path,
    simplify_points,
    SIMPLIFY_EPSILON,
    _perp_distance,
)
from smartboard.view import WhiteboardView  # noqa: E402
from smartboard.main_window import MainWindow, _PALETTE  # noqa: E402

# 保证整个测试过程存在唯一的 QApplication 实例。
# 本环境（PySide6 6.12 + offscreen）下，构造 QGraphicsScene 必须先有 QApplication，
# 否则会段错误。统一在模块加载时创建一次，既修复既有用例，也覆盖新增用例，
# 不改变任何被测行为。
_QAPP = QApplication.instance() or QApplication([])


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


class ShortcutTest(unittest.TestCase):
    """验证键盘快捷键已注册且正确绑定到撤销/重做/清空/橡皮擦等操作。"""

    @staticmethod
    def _app():
        from PySide6.QtWidgets import QApplication
        return QApplication.instance() or QApplication([])

    def _win(self):
        self._app()
        return MainWindow()

    def test_shortcuts_registered(self):
        """核心操作都应有对应快捷键，且 key 序列正确。"""
        from PySide6.QtGui import QShortcut
        win = self._win()
        registered = {sc.key().toString() for sc in win.findChildren(QShortcut)}
        expected = {"Ctrl+Z", "Ctrl+Shift+Z", "Ctrl+Y", "Ctrl+L", "E",
                    "Ctrl+S", "Ctrl+O"}
        self.assertTrue(expected.issubset(registered),
                        "缺少快捷键: %s" % (expected - registered))
        win.close()

    def test_undo_redo_shortcut_triggers(self):
        """发射 Ctrl+Z / 重做 快捷键信号，确定性验证其确实调用了对应操作。"""
        from PySide6.QtGui import QShortcut
        win = self._win()
        scene = win.board.scene()
        scene.add_stroke(Stroke(QColor("black"), 4, [QPointF(0, 0), QPointF(5, 5)]))
        self.assertEqual(scene.count(), 1)

        undo_sc = next(s for s in win.findChildren(QShortcut)
                       if s.key().toString() == "Ctrl+Z")
        undo_sc.activated.emit()
        self.assertEqual(scene.count(), 0)
        self.assertTrue(scene.can_redo())

        redo_sc = next(s for s in win.findChildren(QShortcut)
                       if s.key().toString() in ("Ctrl+Shift+Z", "Ctrl+Y"))
        redo_sc.activated.emit()
        self.assertEqual(scene.count(), 1)
        self.assertFalse(scene.can_redo())
        win.close()

    def test_clear_and_eraser_shortcuts(self):
        """Ctrl+L 清空、E 切换橡皮擦，发射信号验证绑定正确。"""
        from PySide6.QtGui import QShortcut
        win = self._win()
        scene = win.board.scene()
        scene.add_stroke(Stroke(QColor("black"), 4, [QPointF(0, 0), QPointF(5, 5)]))
        self.assertTrue(win._clear_act.isEnabled())

        clear_sc = next(s for s in win.findChildren(QShortcut)
                        if s.key().toString() == "Ctrl+L")
        clear_sc.activated.emit()
        self.assertEqual(scene.count(), 0)
        self.assertFalse(win._clear_act.isEnabled())

        # 橡皮快捷键应翻转 eraser 状态（关闭->开启->关闭）
        self.assertFalse(win.board.eraser)
        eraser_sc = next(s for s in win.findChildren(QShortcut)
                         if s.key().toString() == "E")
        eraser_sc.activated.emit()
        self.assertTrue(win.board.eraser)
        eraser_sc.activated.emit()
        self.assertFalse(win.board.eraser)
        win.close()

    def test_real_key_events_route_to_shortcuts(self):
        """端到端：真实键盘事件（QTest）应触发撤销/重做/橡皮/清空。"""
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication

        win = self._win()
        win.show()
        app = QApplication.instance()
        app.setActiveWindow(win)
        win.activateWindow()
        win.raise_()

        scene = win.board.scene()
        scene.add_stroke(Stroke(QColor("black"), 4, [QPointF(0, 0), QPointF(5, 5)]))
        self.assertEqual(scene.count(), 1)

        # Ctrl+Z -> 撤销
        QTest.keyClick(win, Qt.Key_Z, Qt.ControlModifier)
        app.processEvents()
        self.assertEqual(scene.count(), 0)
        self.assertTrue(scene.can_redo())

        # Ctrl+Shift+Z -> 重做
        QTest.keyClick(win, Qt.Key_Z, Qt.ControlModifier | Qt.ShiftModifier)
        app.processEvents()
        self.assertEqual(scene.count(), 1)

        # E -> 切换橡皮擦
        QTest.keyClick(win, Qt.Key_E)
        app.processEvents()
        self.assertTrue(win.board.eraser)

        # Ctrl+L -> 清空
        QTest.keyClick(win, Qt.Key_L, Qt.ControlModifier)
        app.processEvents()
        self.assertEqual(scene.count(), 0)
        win.close()


class ZOrderTest(unittest.TestCase):
    """验证「绘制顺序决定层叠」，修复橡皮擦遮挡后画笔迹的缺陷。"""

    @staticmethod
    def _z(scene, stroke):
        return scene._items[stroke].zValue()

    def test_z_increases_with_draw_order(self):
        """每新增一笔 z 单调递增（橡皮与普通笔迹一视同仁）。"""
        scene = WhiteboardScene()
        a = Stroke(QColor("black"), 4, [QPointF(0, 0), QPointF(5, 5)])
        b = Stroke(QColor("red"), 4, [QPointF(0, 0), QPointF(6, 6)])
        scene.add_stroke(a)
        scene.add_stroke(b)
        self.assertEqual(self._z(scene, a), 0)
        self.assertEqual(self._z(scene, b), 1)
        self.assertLess(self._z(scene, a), self._z(scene, b))

    def test_eraser_stacks_between_normal_strokes(self):
        """回归：橡皮只压住它之前的笔迹；后画的笔迹应位于橡皮之上。"""
        scene = WhiteboardScene()
        before = Stroke(QColor("black"), 4, [QPointF(0, 0), QPointF(50, 50)])
        eraser = Stroke(QColor("black"), 20,
                        [QPointF(10, 10), QPointF(40, 40)], eraser=True)
        after = Stroke(QColor("red"), 4, [QPointF(0, 0), QPointF(45, 45)])
        scene.add_stroke(before)
        scene.add_stroke(eraser)
        scene.add_stroke(after)

        zb = self._z(scene, before)
        ze = self._z(scene, eraser)
        za = self._z(scene, after)
        self.assertLess(zb, ze)   # 橡皮遮住“之前”的笔迹（擦除生效）
        self.assertLess(ze, za)   # 橡皮不再遮住“之后”新画的笔迹（修复点）
        self.assertEqual(scene.count(), 3)

    def test_undo_redo_preserves_z(self):
        """撤销后重做，笔迹以原始 z 回到原绘制位置。"""
        scene = WhiteboardScene()
        s = Stroke(QColor("blue"), 4, [QPointF(0, 0), QPointF(5, 5)])
        scene.add_stroke(s)
        z0 = self._z(scene, s)
        self.assertTrue(scene.undo())
        self.assertTrue(scene.redo())
        self.assertEqual(self._z(scene, s), z0)
        self.assertEqual(scene.count(), 1)

    def test_clear_resets_z_counter(self):
        """清空后 z 计数归零，新笔迹从 0 开始。"""
        scene = WhiteboardScene()
        scene.add_stroke(Stroke(QColor("black"), 4, [QPointF(0, 0)]))
        scene.add_stroke(Stroke(QColor("red"), 4, [QPointF(1, 1)]))
        scene.clear_all()
        s = Stroke(QColor("green"), 4, [QPointF(0, 0)])
        scene.add_stroke(s)
        self.assertEqual(self._z(scene, s), 0)


class ObjectEraserTest(unittest.TestCase):
    """右键精确擦除：删除光标下整条笔迹，且可被重做恢复。"""

    @staticmethod
    def _app():
        from PySide6.QtWidgets import QApplication
        return QApplication.instance() or QApplication([])

    def test_remove_stroke_by_reference(self):
        scene = WhiteboardScene()
        a = Stroke(QColor("black"), 4, [QPointF(0, 0), QPointF(5, 5)])
        b = Stroke(QColor("red"), 4, [QPointF(10, 0), QPointF(15, 5)])
        scene.add_stroke(a)
        scene.add_stroke(b)
        self.assertEqual(scene.count(), 2)

        self.assertTrue(scene.remove_stroke(a))
        self.assertEqual(scene.count(), 1)
        self.assertIn(b, scene._strokes)
        self.assertNotIn(a, scene._strokes)

        # 被删除的笔迹进入重做栈，可恢复
        self.assertTrue(scene.can_redo())
        self.assertTrue(scene.redo())
        self.assertEqual(scene.count(), 2)
        self.assertIn(a, scene._strokes)

    def test_remove_stroke_cleans_item_and_maps(self):
        scene = WhiteboardScene()
        s = Stroke(QColor("black"), 4, [QPointF(0, 0), QPointF(5, 5)])
        scene.add_stroke(s)
        item = scene._items[s]
        self.assertEqual(len(scene.items()), 1)

        scene.remove_stroke(s)
        self.assertEqual(scene.count(), 0)
        self.assertEqual(len(scene.items()), 0)
        self.assertNotIn(s, scene._items)
        self.assertNotIn(item, scene._stroke_of)

    def test_erase_stroke_at_scene(self):
        self._app()
        view = WhiteboardView()
        view.resize(400, 300)
        view.show()
        view.centerOn(QPointF(0, 0))
        view.scene().add_stroke(
            Stroke(QColor("black"), 6, [QPointF(0, 0), QPointF(10, 10), QPointF(0, 10)])
        )
        self.assertEqual(view.scene().count(), 1)

        # 命中笔迹中段 -> 删除
        self.assertTrue(view._erase_stroke_at_scene(QPointF(5, 5)))
        self.assertEqual(view.scene().count(), 0)

        # 空白区域 -> 无操作、返回 False
        self.assertFalse(view._erase_stroke_at_scene(QPointF(9999, 9999)))

    def test_right_click_deletes_stroke(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        self._app()
        view = WhiteboardView()
        view.resize(600, 400)
        view.show()
        view.centerOn(QPointF(0, 0))
        app = self._app()
        app.processEvents()

        view.scene().add_stroke(
            Stroke(QColor("black"), 8, [QPointF(0, 0), QPointF(5, 5), QPointF(-5, 5)])
        )
        before = view.scene().count()
        self.assertEqual(before, 1)

        # 视口中心对应场景原点附近 -> 右键整条删除
        QTest.mouseClick(view.viewport(), Qt.RightButton,
                         pos=view.viewport().rect().center())
        app.processEvents()
        self.assertEqual(view.scene().count(), before - 1)
        # 删除进入重做栈，可恢复（Ctrl+Shift+Z / Ctrl+Y）
        self.assertTrue(view.scene().can_redo())
        self.assertTrue(view.scene().redo())
        self.assertEqual(view.scene().count(), before)


class SimplificationTest(unittest.TestCase):
    """验证提交笔迹时的 RDP 抽稀：减小存储/渲染开销，且视觉偏差受控。

    关键不变量：被抽掉的原采样点到简化后折线的距离均不超过 epsilon。
    """

    @staticmethod
    def _point_to_polyline_max_dev(orig, simplified):
        """原折线所有点到简化后折线的最大距离（RDP 保证 <= epsilon）。"""
        # 把简化点序列视为折线，计算每段对原点的距离上限
        s = simplified
        if len(s) < 2:
            # 退化：简化为单点，偏差即到该点的最大距离
            return max(((p.x() - s[0].x()) ** 2 + (p.y() - s[0].y()) ** 2) ** 0.5
                       for p in orig)
        worst = 0.0
        for p in orig:
            seg_min = min(_perp_distance(p, s[i], s[i + 1])
                          for i in range(len(s) - 1))
            worst = max(worst, seg_min)
        return worst

    def test_collapses_straight_line(self):
        """近共线长线应被抽稀到仅首尾两点。"""
        pts = [QPointF(x, 0.0) for x in range(0, 500)]
        s = simplify_points(pts, 1.0)
        self.assertEqual(len(s), 2)
        self.assertAlmostEqual(s[0].x(), 0.0)
        self.assertAlmostEqual(s[-1].x(), 499.0)

    def test_preserves_sharp_corner(self):
        """直角拐点必须被保留（不能把拐角抹平）。"""
        pts = ([QPointF(x, 0.0) for x in range(0, 100)]
               + [QPointF(99.0, y) for y in range(1, 100)])
        s = simplify_points(pts, 1.0)
        self.assertGreaterEqual(len(s), 3)
        has_corner = any(abs(p.x() - 99.0) < 1e-6 and abs(p.y()) < 1e-6 for p in s)
        self.assertTrue(has_corner, "拐点 (99,0) 丢失")

    def test_max_deviation_within_epsilon(self):
        """正弦曲线：抽稀后最大偏差不超过 epsilon，且点数明显减少。"""
        pts = [QPointF(i, 50.0 * math.sin(i / 5.0)) for i in range(200)]
        eps = 2.0
        s = simplify_points(pts, eps)
        self.assertLess(len(s), len(pts))
        self.assertLessEqual(
            self._point_to_polyline_max_dev(pts, s), eps + 1e-6
        )

    def test_endpoints_always_kept(self):
        """任意输入端点必保留，避免笔迹首尾错位。"""
        random_pts = [QPointF(math.cos(i), math.sin(i * 1.3)) for i in range(50)]
        s = simplify_points(random_pts, 1.0)
        self.assertAlmostEqual(s[0].x(), random_pts[0].x())
        self.assertAlmostEqual(s[0].y(), random_pts[0].y())
        self.assertAlmostEqual(s[-1].x(), random_pts[-1].x())
        self.assertAlmostEqual(s[-1].y(), random_pts[-1].y())

    def test_small_input_not_simplified(self):
        """点数 <= 2 或 epsilon <= 0 时不抽稀，原样返回。"""
        two = [QPointF(0, 0), QPointF(10, 10)]
        self.assertEqual(len(simplify_points(two, 5.0)), 2)
        long_pts = [QPointF(x, 0.0) for x in range(0, 100)]
        self.assertEqual(len(simplify_points(long_pts, 0.0)), len(long_pts))

    def test_view_commit_simplifies(self):
        """视图提交路径确实使用抽稀：长直线笔迹提交后仅剩首尾两点。"""
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        view = WhiteboardView()
        view.simplify_epsilon = 1.0
        view._current = Stroke(
            QColor("#1A1A1A"), 4,
            [QPointF(x, 0.0) for x in range(0, 300)], eraser=False,
        )
        before = view.scene().count()
        view._commit_current()
        self.assertEqual(view.scene().count(), before + 1)
        committed = view.scene()._strokes[-1]
        self.assertEqual(len(committed.points), 2)  # 抽稀为端点

    def test_view_commit_keeps_short_stroke(self):
        """短笔迹（< 3 点）不受抽稀影响，正常提交。"""
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        view = WhiteboardView()
        view._current = Stroke(QColor("#1A1A1A"), 4,
                               [QPointF(0, 0), QPointF(5, 5)], eraser=False)
        view._commit_current()
        self.assertEqual(view.scene().count(), 1)
        self.assertEqual(len(view.scene()._strokes[-1].points), 2)

    def test_simplify_epsilon_default(self):
        self.assertGreater(SIMPLIFY_EPSILON, 0)


class ExportImageTest(unittest.TestCase):
    """验证「导出为图片」：仅渲染笔迹内容区域，所见即所得，headless 可验证。

    覆盖：导出成功、生成合法 PNG、尺寸贴合内容区、笔迹与背景均正确呈现。
    """

    @staticmethod
    def _app():
        from PySide6.QtWidgets import QApplication
        return QApplication.instance() or QApplication([])

    @staticmethod
    def _tmp_png():
        fd, path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        return path

    def test_export_empty_scene_valid_png(self):
        """空场景导出应生成合法的 200×200 PNG（回退默认画布）。"""
        self._app()
        view = WhiteboardView()
        path = self._tmp_png()
        try:
            self.assertTrue(view.export_image(path, "PNG"))
            self.assertGreater(os.path.getsize(path), 0)
            with open(path, "rb") as f:
                magic = f.read(8)
            self.assertEqual(magic, b"\x89PNG\r\n\x1a\n")
            img = QImage(path)
            self.assertEqual(img.width(), 200)
            self.assertEqual(img.height(), 200)
        finally:
            os.remove(path)

    def test_export_size_matches_content_rect(self):
        """导出图片尺寸应与内容矩形（含留白）严格一致。"""
        self._app()
        view = WhiteboardView()
        view.scene().add_stroke(
            Stroke(QColor("#1A1A1A"), 4, [QPointF(0, 0), QPointF(100, 50)])
        )
        rect = view.content_rect()
        expected_w = max(1, int(math.ceil(rect.width())))
        expected_h = max(1, int(math.ceil(rect.height())))
        path = self._tmp_png()
        try:
            self.assertTrue(view.export_image(path, "PNG"))
            img = QImage(path)
            self.assertEqual(img.width(), expected_w)
            self.assertEqual(img.height(), expected_h)
        finally:
            os.remove(path)

    def test_export_renders_content_and_background(self):
        """导出图片应同时包含笔迹像素与背景底色（所见即所得）。"""
        self._app()
        view = WhiteboardView()
        view.scene().add_stroke(
            Stroke(QColor("#1A1A1A"), 6, [QPointF(0, 0), QPointF(60, 40)])
        )
        path = self._tmp_png()
        try:
            self.assertTrue(view.export_image(path, "PNG"))
            img = QImage(path)
            self.assertGreater(img.width(), 0)
            self.assertGreater(img.height(), 0)

            bg = view.scene().backgroundBrush().color()
            non_bg = 0
            step_x = max(1, img.width() // 20)
            step_y = max(1, img.height() // 20)
            for y in range(0, img.height(), step_y):
                for x in range(0, img.width(), step_x):
                    if img.pixelColor(x, y) != bg:
                        non_bg += 1
            self.assertGreater(non_bg, 0, "导出图片应至少包含一处笔迹像素")
        finally:
            os.remove(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)

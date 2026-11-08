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

from PySide6.QtCore import Qt, QPointF, QPoint  # noqa: E402
from PySide6.QtGui import QColor, QImage, QAction, QTransform  # noqa: E402
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
from smartboard.pen_cursor import PenCursorOverlay  # noqa: E402
from smartboard.main_window import MainWindow, _PALETTE  # noqa: E402

# 保证整个测试过程存在唯一的 QApplication 实例。
# 本环境（PySide6 6.12 + offscreen）下，构造 QGraphicsScene 必须先有 QApplication，
# 否则会段错误。统一在模块加载时创建一次，既修复既有用例，也覆盖新增用例，
# 不改变任何被测行为。
_QAPP = QApplication.instance() or QApplication([])

# 退出前有序拆除所有顶层 Qt 对象并退出事件循环，规避 PySide6 在解释器终析阶段
# 偶发的 bool_dealloc 引用计数崩溃（SIGABRT），保证测试退出码为 0、结论干净。
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
        # 清空现已可撤销（Ctrl+Z 防误清）：撤销按钮应可用，清空按钮因空白板禁用
        self.assertTrue(win._undo_act.isEnabled())
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


class AIToolbarTest(unittest.TestCase):
    """验证 AI 教学联动入口已接入主窗口（不触发阻塞对话框）。"""

    @staticmethod
    def _app():
        from PySide6.QtWidgets import QApplication
        return QApplication.instance() or QApplication([])

    def test_ai_action_present(self):
        self._app()
        win = MainWindow()
        actions = {a.text() for a in win.findChildren(QAction)}
        self.assertIn("AI 答疑", actions)
        self.assertIsNotNone(win._ai)
        win.close()

    def test_ai_context_from_live_scene(self):
        self._app()
        win = MainWindow()
        win.board.scene().add_stroke(
            Stroke(QColor("#1E88E5"), 8, [QPointF(1, 1), QPointF(9, 9)])
        )
        from smartboard.ai_link import board_dict_to_text
        ctx = board_dict_to_text(win.board.scene().to_dict())
        self.assertIn("第1条笔迹", ctx)
        self.assertIn("#1e88e5", ctx)
        win.close()


class StatusBarTest(unittest.TestCase):
    """验证底部状态栏实时反映工具/颜色/线宽/笔迹数与可撤销重做状态。"""

    @staticmethod
    def _app():
        from PySide6.QtWidgets import QApplication
        return QApplication.instance() or QApplication([])

    def _win(self):
        self._app()
        return MainWindow()

    def test_statusbar_widget_present(self):
        """主窗口应常驻一个状态标签部件，供实时状态展示。"""
        self._app()
        win = MainWindow()
        self.assertIsNotNone(win.statusBar())
        self.assertTrue(hasattr(win, "_status_label"))
        from PySide6.QtWidgets import QLabel
        self.assertIsInstance(win._status_label, QLabel)
        win.close()

    def test_status_initial_state(self):
        """初始空白板：画笔模式、0 条笔迹、不可撤销/重做。"""
        win = self._win()
        text = win._status_label.text()
        self.assertIn("画笔", text)
        self.assertIn("笔迹 0 条", text)
        self.assertIn("不可撤销", text)
        self.assertIn("不可重做", text)
        win.close()

    def test_status_reflects_color_and_width(self):
        """选色与改线宽应即时反映到状态栏。"""
        win = self._win()
        win._pick_color("#E53935")
        win._on_width(12)
        text = win._status_label.text()
        self.assertIn("E53935", text)        # 颜色已更新
        self.assertIn("12px", text)          # 线宽已更新
        self.assertIn("画笔", text)          # 仍在画笔模式
        win.close()

    def test_status_reflects_eraser_mode(self):
        """切换橡皮擦：状态栏应显示橡皮擦并隐藏颜色段。"""
        win = self._win()
        win._toggle_eraser(True)
        text = win._status_label.text()
        self.assertIn("橡皮擦", text)
        self.assertNotIn("颜色", text)
        # 关闭橡皮擦后应恢复画笔与颜色显示
        win._toggle_eraser(False)
        self.assertIn("画笔", win._status_label.text())
        self.assertIn("颜色", win._status_label.text())
        win.close()

    def test_status_reflects_stroke_count_and_undo_redo(self):
        """增/删笔迹应驱动状态栏的计数与可撤销/重做文案。"""
        win = self._win()
        scene = win.board.scene()
        self.assertIn("笔迹 0 条", win._status_label.text())

        scene.add_stroke(Stroke(QColor("black"), 4, [QPointF(0, 0), QPointF(5, 5)]))
        text = win._status_label.text()
        self.assertIn("笔迹 1 条", text)
        self.assertIn("可撤销", text)
        self.assertIn("不可重做", text)

        self.assertTrue(scene.undo())
        text = win._status_label.text()
        self.assertIn("笔迹 0 条", text)
        self.assertIn("不可撤销", text)
        self.assertIn("可重做", text)
        win.close()


class PenCursorTest(unittest.TestCase):
    """验证画笔尺寸预览环：辅助书写预判，且不破坏既有书写/导出管线。"""

    @staticmethod
    def _app():
        from PySide6.QtWidgets import QApplication
        return QApplication.instance() or QApplication([])

    def test_overlay_exists_and_transparent(self):
        """叠加层应存在、对鼠标事件透明、且不是场景图元（导出零侵入）。"""
        self._app()
        view = WhiteboardView()
        self.assertTrue(hasattr(view, "pen_cursor"))
        self.assertIsInstance(view.pen_cursor, PenCursorOverlay)
        # 关键不变量：绝不拦截鼠标事件，否则会破坏书写/预览/右键擦除
        self.assertTrue(view.pen_cursor.testAttribute(Qt.WA_TransparentForMouseEvents))
        # 叠加层不是 QGraphicsItem，因此不会被 scene().render 卷入导出图片
        self.assertNotIn(view.pen_cursor, view.scene().items())

    def test_diameter_tracks_pen_width(self):
        """圆环直径应实时跟随画笔线宽（1–40px 范围）。"""
        self._app()
        view = WhiteboardView()
        view.set_pen_width(20)
        self.assertAlmostEqual(view.pen_cursor.diameter(), 20.0)
        view.set_pen_width(1)
        self.assertAlmostEqual(view.pen_cursor.diameter(), 1.0)
        view.set_pen_width(40)
        self.assertAlmostEqual(view.pen_cursor.diameter(), 40.0)

    def test_color_matches_pen(self):
        """圆环颜色应与当前画笔颜色一致。"""
        self._app()
        view = WhiteboardView()
        view.set_pen_color("#E53935")
        self.assertEqual(view.pen_cursor.pen_color().name().upper(), "#E53935")

    def test_visibility_pen_vs_eraser(self):
        """画笔模式（鼠标在内）显示、橡皮模式隐藏。"""
        self._app()
        view = WhiteboardView()
        view.show()  # 使窗口层级可见，isVisible() 才能反映叠加层自身显隐状态
        view._mouse_inside = True
        view._update_cursor_visibility()      # 画笔 + 鼠标在内 -> 显示
        self.assertTrue(view.pen_cursor.isVisible())

        view.set_eraser(True)                 # 切橡皮 -> 隐藏
        self.assertFalse(view.pen_cursor.isVisible())

        view.set_eraser(False)                # 切回画笔 -> 显示
        self.assertTrue(view.pen_cursor.isVisible())

    def test_move_to_centers_ring(self):
        """move_to 应把圆环中心对齐到给定视口坐标。"""
        self._app()
        view = WhiteboardView()
        view.pen_cursor.set_pen(10, "#1A1A1A")
        view.pen_cursor.move_to(QPoint(100, 80))
        # 直径 10 -> 半径 5，左上角 = 中心 - 半径
        self.assertEqual(view.pen_cursor.x(), 95)
        self.assertEqual(view.pen_cursor.y(), 75)
        self.assertEqual(view.pen_cursor.width(), 10)


class ZoomPanTest(unittest.TestCase):
    """验证新增的画布缩放 / 平移交互：仅作用于视图变换，不触碰场景数据。"""

    @staticmethod
    def _app():
        from PySide6.QtWidgets import QApplication
        return QApplication.instance() or QApplication([])

    def _view(self):
        self._app()
        v = WhiteboardView()
        v.resize(600, 400)
        v.show()
        v.centerOn(QPointF(0, 0))
        return v

    def test_zoom_increases_scale_factor(self):
        """缩放应使视图变换的缩放系数按 factor 倍变化（默认以视图中心为锚）。"""
        v = self._view()
        before = v.transform().m11()
        v.zoomBy(1.25)
        after = v.transform().m11()
        self.assertAlmostEqual(after, before * 1.25, delta=1e-6)

    def test_zoom_anchor_keeps_cursor_scene_point(self):
        """以光标为锚缩放：缩放前后光标处的场景坐标基本不动（聚焦光标）。

        用像素空间校验——缩放后“原光标场景点”应仍落在光标像素附近（容忍滚动条取整）。
        """
        v = self._view()
        cursor = QPoint(420, 180)
        scene_at_cursor = v.mapToScene(cursor)
        v.zoomBy(1.4, cursor)
        back = v.mapFromScene(scene_at_cursor)
        self.assertAlmostEqual(back.x(), cursor.x(), delta=1.5)
        self.assertAlmostEqual(back.y(), cursor.y(), delta=1.5)

    def test_pan_translates_content(self):
        """中键平移 120 屏幕像素：视口中心对应的场景坐标左移约 120/scale。"""
        v = self._view()
        center = v.viewport().rect().center()
        before = v.mapToScene(center)
        v.pan(120, 0)
        after = v.mapToScene(center)
        f = v.transform().m11()
        self.assertAlmostEqual(after.x(), before.x() - 120.0 / f, delta=1.5)
        self.assertAlmostEqual(after.y(), before.y(), delta=1.5)

    def test_reset_view_returns_to_identity(self):
        """缩放 + 平移后：reset_view 应使缩放系数归 1 且原点回到视图中心。"""
        v = self._view()
        v.zoomBy(1.8)
        v.pan(50, 30)
        v.reset_view()
        self.assertAlmostEqual(v.transform().m11(), 1.0, delta=1e-6)
        # 原点应大致回到视图中心（容忍 offscreen 下场景矩形对齐的取整）
        center_scene = v.mapToScene(v.viewport().rect().center())
        self.assertAlmostEqual(center_scene.x(), 0.0, delta=1.5)
        self.assertAlmostEqual(center_scene.y(), 0.0, delta=1.5)

    def test_zoom_pan_do_not_alter_strokes(self):
        """回归：缩放/平移不应改变场景笔迹数或任一笔迹的点集（仅视图变换）。"""
        v = self._view()
        s = Stroke(QColor("#1A1A1A"), 6, [QPointF(0, 0), QPointF(40, 30), QPointF(10, 60)])
        v.scene().add_stroke(s)
        pts_before = [QPointF(p) for p in s.points]

        v.zoomBy(1.5)
        v.pan(80, -40)
        v.zoomBy(0.8, QPoint(300, 200))

        self.assertEqual(v.scene().count(), 1)
        self.assertEqual(len(v.scene()._strokes[0].points), len(pts_before))
        for a, b in zip(v.scene()._strokes[0].points, pts_before):
            self.assertAlmostEqual(a.x(), b.x(), delta=1e-9)
            self.assertAlmostEqual(a.y(), b.y(), delta=1e-9)


class ZoomAwareWritingTest(unittest.TestCase):
    """验证缩放感知的书写采样与预览环（书写体验，headless 可验证）。

    背景：原实现采样阈值固定 1 场景像素、预览环直径固定=线宽（屏幕像素）。
    这导致放大时笔迹因采点被丢而变锯齿、预览环不再匹配实际屏幕笔宽。
    修复后：采样阈值随缩放反比（屏幕约 1px 恒定）；环直径=线宽×scale。
    """

    @staticmethod
    def _app():
        from PySide6.QtWidgets import QApplication
        return QApplication.instance() or QApplication([])

    def _view(self):
        self._app()
        v = WhiteboardView()
        v.resize(600, 400)
        v.show()
        v.centerOn(QPointF(0, 0))
        return v

    def test_sample_threshold_scales_with_zoom(self):
        """采样阈值随缩放反比：scale1→1.0, scale2→0.5, scale0.5→2.0。"""
        v = self._view()
        self.assertAlmostEqual(v._scene_sample_threshold(), 1.0, delta=1e-9)
        v.setTransform(QTransform().scale(2.0, 2.0))
        self.assertAlmostEqual(v._scene_sample_threshold(), 0.5, delta=1e-9)
        v.setTransform(QTransform().scale(0.5, 0.5))
        self.assertAlmostEqual(v._scene_sample_threshold(), 2.0, delta=1e-9)

    def test_cursor_diameter_scales_with_zoom(self):
        """预览环直径随缩放 = 线宽×scale：放大 2× 后直径翻倍，复位后还原。"""
        v = self._view()
        v.set_pen_width(10)
        self.assertAlmostEqual(v.pen_cursor.diameter(), 10.0, delta=1e-9)
        v.zoomBy(2.0)                       # 缩放改变映射
        self.assertAlmostEqual(v.pen_cursor.diameter(), 20.0, delta=1e-9)
        v.reset_view()                      # 缩放归 1
        self.assertAlmostEqual(v.pen_cursor.diameter(), 10.0, delta=1e-9)

    def _start_stroke(self, view, deltas):
        """以视口中心为起点，按 deltas（视口像素增量）驱动左键按下与若干移动。"""
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        vp = view.viewport()
        center = vp.rect().center()
        QTest.mousePress(vp, Qt.LeftButton, pos=center)
        for dx, dy in deltas:
            QTest.mouseMove(vp, QPoint(center.x() + dx, center.y() + dy))
        return center

    def _end_stroke(self, view, last_delta):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        vp = view.viewport()
        center = vp.rect().center()
        QTest.mouseRelease(
            vp, Qt.LeftButton,
            pos=QPoint(center.x() + last_delta[0], center.y() + last_delta[1]),
        )

    def test_sampling_regression_at_scale1(self):
        """回归：scale1 下累计 1px、2px 两跳各满足 1px 阈值 → 采到 3 个采样点。"""
        v = self._view()
        self._start_stroke(v, [(1, 0), (2, 0)])
        # 抬起前 _current 保存完整采样（提交时的 RDP 抽稀不影响此处计数）
        self.assertEqual(len(v._current.points), 3)     # 起点 + 2 跳
        self._end_stroke(v, (2, 0))
        self.assertEqual(v.scene().count(), 1)          # 提交为 1 条笔迹

    def test_zoomed_in_no_longer_drops_samples(self):
        """放大 2× 后，同样的 1px、2px 两跳仍被采样（3 点），证明放大时不再丢点变锯齿。

        对照（修复前）：scale2 下固定 1 场景像素阈值会把中间那跳（0.5 场景像素）丢弃，
        只剩起点与终点 2 点。本断言确认修复后保留 3 点，笔迹在放大时依然平滑。
        """
        v = self._view()
        v.zoomBy(2.0)
        v.centerOn(QPointF(0, 0))
        self._start_stroke(v, [(1, 0), (2, 0)])
        self.assertEqual(len(v._current.points), 3)     # 放大下仍采到 3 点
        self._end_stroke(v, (2, 0))
        self.assertEqual(v.scene().count(), 1)


class GridBackgroundTest(unittest.TestCase):
    """验证书写辅助网格：默认关闭（零侵入），可开关，且渲染/导出中包含网格像素。"""

    @staticmethod
    def _app():
        from PySide6.QtWidgets import QApplication
        return QApplication.instance() or QApplication([])

    @staticmethod
    def _win():
        GridBackgroundTest._app()
        return MainWindow()

    @staticmethod
    def _tmp_png():
        fd, path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        return path

    @staticmethod
    def _count_non_bg(path, bg):
        """统计与背景色不同的像素数（采样，用于判断网格是否落入导出图）。"""
        img = QImage(path)
        non_bg = 0
        step_x = max(1, img.width() // 20)
        step_y = max(1, img.height() // 20)
        for y in range(0, img.height(), step_y):
            for x in range(0, img.width(), step_x):
                if img.pixelColor(x, y) != bg:
                    non_bg += 1
        return non_bg

    def test_grid_off_by_default(self):
        """回归：网格默认关闭，对既有书写/导出零侵入。"""
        self._app()
        scene = WhiteboardScene()
        self.assertFalse(scene.is_grid_enabled())

    def test_grid_toggle_flag(self):
        scene = WhiteboardScene()
        self.assertFalse(scene.is_grid_enabled())
        scene.set_grid_enabled(True)
        self.assertTrue(scene.is_grid_enabled())
        scene.set_grid_enabled(False)
        self.assertFalse(scene.is_grid_enabled())

    def test_grid_renders_in_export(self):
        """开启网格后，空场景导出也应含网格像素（非背景）。"""
        self._app()
        view = WhiteboardView()
        view.scene().set_grid_enabled(True)
        path = self._tmp_png()
        try:
            self.assertTrue(view.export_image(path, "PNG"))
            bg = view.scene().backgroundBrush().color()
            self.assertGreater(self._count_non_bg(path, bg), 0)
        finally:
            os.remove(path)

    def test_no_grid_when_disabled(self):
        """默认关闭网格时，空场景导出应只有纯背景（无非背景像素）。"""
        self._app()
        view = WhiteboardView()
        self.assertFalse(view.scene().is_grid_enabled())
        path = self._tmp_png()
        try:
            self.assertTrue(view.export_image(path, "PNG"))
            bg = view.scene().backgroundBrush().color()
            self.assertEqual(self._count_non_bg(path, bg), 0)
        finally:
            os.remove(path)

    def test_grid_toggle_via_mainwindow(self):
        """工具栏「网格」按钮应同步开启/关闭场景网格与按钮勾选态。"""
        self._app()
        win = self._win()
        self.assertFalse(win.board.is_grid_enabled())
        self.assertFalse(win._grid_act.isChecked())

        win._toggle_grid(True)
        self.assertTrue(win.board.is_grid_enabled())
        self.assertTrue(win._grid_act.isChecked())

        win._toggle_grid(False)
        self.assertFalse(win.board.is_grid_enabled())
        self.assertFalse(win._grid_act.isChecked())
        win.close()

    def test_grid_shortcut_toggles(self):
        """快捷键 G 应切换网格开关。"""
        from PySide6.QtGui import QShortcut
        win = self._win()
        self.assertFalse(win.board.is_grid_enabled())
        g_sc = next(s for s in win.findChildren(QShortcut)
                    if s.key().toString() == "G")
        g_sc.activated.emit()
        self.assertTrue(win.board.is_grid_enabled())
        g_sc.activated.emit()
        self.assertFalse(win.board.is_grid_enabled())
        win.close()


class RobustLoadTest(unittest.TestCase):
    """验证载入的原子性：损坏/非法文件不会清空当前白板（数据丢失防护）。

    背景与修复：旧实现 load_dict 先 clear_all() 再逐条解析，若文件中任一笔迹
    格式非法，from_dict 抛错时当前白板已被清空且无法用撤销恢复——载入损坏文件
    会永久抹掉正在编辑的内容。新实现先完整校验再替换，任何非法都整体失败且不丢盘。
    """

    @staticmethod
    def _app():
        from PySide6.QtWidgets import QApplication
        return QApplication.instance() or QApplication([])

    def test_valid_load_replaces_content(self):
        """合法文件：覆盖当前内容，且行为与原实现一致。"""
        self._app()
        scene = WhiteboardScene()
        scene.add_stroke(Stroke(QColor("black"), 4, [QPointF(0, 0), QPointF(5, 5)]))
        data = {"version": 1, "strokes": [
            {"color": "#E53935", "width": 6, "points": [[1, 1], [9, 9]]}]}
        scene.load_dict(data)
        self.assertEqual(scene.count(), 1)
        self.assertEqual(scene._strokes[0].color.name().upper(), "#E53935")

    def test_malformed_point_shape_keeps_current_board(self):
        """载入含非法笔迹（点坐标非二元组）应抛 ValueError，且原白板不被清空。"""
        self._app()
        scene = WhiteboardScene()
        scene.add_stroke(Stroke(QColor("black"), 4, [QPointF(0, 0), QPointF(5, 5)]))
        before = scene.count()
        bad = {"strokes": [
            {"color": "#E53935", "width": 6, "points": [[1, 1], [9, 9]]},
            {"color": "#1E88E5", "width": 4, "points": [[1, 2, 3]]},  # 三元组非法
        ]}
        with self.assertRaises(ValueError):
            scene.load_dict(bad)
        # 关键不变量：原白板未被清空（原子性保护）
        self.assertEqual(scene.count(), before)
        self.assertEqual(len(scene._strokes), before)
        self.assertEqual(len(scene._items), before)

    def test_non_dict_root_raises_and_preserves(self):
        """根节点不是对象（如 JSON 数组/标量）应抛错且不丢当前白板。"""
        self._app()
        scene = WhiteboardScene()
        scene.add_stroke(Stroke(QColor("black"), 4, [QPointF(0, 0)]))
        before = scene.count()
        with self.assertRaises(ValueError):
            scene.load_dict([1, 2, 3])
        self.assertEqual(scene.count(), before)

    def test_atomic_no_partial_attach(self):
        """原子性：即便前几条合法，只要有一条非法，整批都不挂入，白板保持原样。"""
        self._app()
        scene = WhiteboardScene()
        scene.add_stroke(Stroke(QColor("black"), 4, [QPointF(0, 0)]))
        before = scene.count()
        bad = {"strokes": [
            {"color": "#E53935", "width": 6, "points": [[1, 1], [9, 9]]},  # 合法
            {"color": "#1E88E5", "width": 4, "points": "not-a-list"},      # 非法
        ]}
        with self.assertRaises(ValueError):
            scene.load_dict(bad)
        # 合法的第一条也未被挂入（无半途提交）
        self.assertEqual(scene.count(), before)
        self.assertEqual(len(scene._items), before)


class ClearUndoTest(unittest.TestCase):
    """验证「清空」可整批撤销：杜绝 Ctrl+L 误清后板书无法恢复的数据丢失风险。

    背景与修复：旧实现 clear_all 直接丢弃当前笔迹且清空重做栈，清空后无法撤销，
    课堂误按 Ctrl+L 会永久抹掉板书。新实现在清空前快照笔迹，Ctrl+Z 整批恢复，
    且恢复后保留各自 z、可继续在其之上书写；载入覆盖同样可一键还原。
    """

    @staticmethod
    def _app():
        from PySide6.QtWidgets import QApplication
        return QApplication.instance() or QApplication([])

    def _win(self):
        self._app()
        return MainWindow()

    def test_clear_then_undo_restores_count_and_content(self):
        """清空后撤销应整批恢复笔迹数量与内容（含颜色/橡皮属性）。"""
        self._app()
        scene = WhiteboardScene()
        scene.add_stroke(Stroke(QColor("#1A1A1A"), 4, [QPointF(0, 0), QPointF(5, 5)]))
        scene.add_stroke(Stroke(QColor("#E53935"), 6,
                                [QPointF(1, 1), QPointF(9, 9)], eraser=True))
        self.assertEqual(scene.count(), 2)
        self.assertTrue(scene.can_undo())

        scene.clear_all()
        self.assertEqual(scene.count(), 0)            # 清空生效
        self.assertTrue(scene.can_undo())             # 清空可撤销（关键不变量）

        self.assertTrue(scene.undo())                 # Ctrl+Z 整批恢复
        self.assertEqual(scene.count(), 2)            # 数量还原
        self.assertAlmostEqual(scene._strokes[0].points[0].x(), 0.0, delta=1e-9)
        self.assertTrue(scene._strokes[1].eraser)     # 橡皮属性还原

    def test_clear_then_undo_preserves_z(self):
        """清空后撤销恢复的笔迹应保持原 z（层叠顺序不变）。"""
        self._app()
        scene = WhiteboardScene()
        a = Stroke(QColor("black"), 4, [QPointF(0, 0), QPointF(5, 5)])
        b = Stroke(QColor("red"), 4, [QPointF(6, 6), QPointF(9, 9)])
        scene.add_stroke(a)
        scene.add_stroke(b)
        za, zb = scene._items[a].zValue(), scene._items[b].zValue()

        scene.clear_all()
        self.assertTrue(scene.undo())
        self.assertEqual(scene._items[a].zValue(), za)
        self.assertEqual(scene._items[b].zValue(), zb)
        self.assertLess(scene._items[a].zValue(), scene._items[b].zValue())

    def test_clear_then_undo_continues_z_counter(self):
        """清空后恢复再继续书写：新笔迹 z 应接在被恢复笔迹之后，不与其冲突。"""
        self._app()
        scene = WhiteboardScene()
        scene.add_stroke(Stroke(QColor("black"), 4, [QPointF(0, 0)]))
        scene.add_stroke(Stroke(QColor("red"), 4, [QPointF(1, 1)]))
        scene.clear_all()
        self.assertTrue(scene.undo())

        # 在恢复后的板面上继续书写，新笔迹 z 应 >= 已有最大 z
        new_s = Stroke(QColor("blue"), 4, [QPointF(2, 2)])
        scene.add_stroke(new_s)
        max_z = max(it.zValue() for it in scene._items.values())
        self.assertGreaterEqual(scene._items[new_s].zValue(), max_z - 1e-9)
        self.assertEqual(scene.count(), 3)

    def test_clear_empty_board_is_noop(self):
        """空白板清空应为无操作：不设置可撤销快照、can_undo 保持 False。"""
        self._app()
        scene = WhiteboardScene()
        self.assertFalse(scene.can_undo())
        scene.clear_all()
        self.assertEqual(scene.count(), 0)
        self.assertFalse(scene.can_undo())
        self.assertFalse(scene.undo())

    def test_double_clear_still_undoable(self):
        """连续两次清空不应崩溃，且仍可整批撤销到最初内容。"""
        self._app()
        scene = WhiteboardScene()
        scene.add_stroke(Stroke(QColor("black"), 4, [QPointF(0, 0)]))
        scene.clear_all()
        scene.clear_all()                 # 第二次清空：旧快照被新（空）覆盖
        self.assertTrue(scene.can_undo())
        self.assertTrue(scene.undo())
        self.assertEqual(scene.count(), 1)
        self.assertAlmostEqual(scene._strokes[0].points[0].x(), 0.0, delta=1e-9)

    def test_clear_button_state_after_clear(self):
        """清空后：清空按钮因空白板禁用，但撤销按钮可用（可一键还原）。"""
        self._app()
        win = self._win()
        win.board.scene().add_stroke(
            Stroke(QColor("#1E88E5"), 6, [QPointF(0, 0), QPointF(10, 10)]))
        self.assertTrue(win._undo_act.isEnabled())
        self.assertTrue(win._clear_act.isEnabled())

        win.board.scene().clear_all()
        self.assertTrue(win._undo_act.isEnabled())     # 可撤销：防误清
        self.assertFalse(win._clear_act.isEnabled())   # 空白板不可再清空
        win.close()

    def test_clear_shortcut_then_undo_shortcut_restores(self):
        """端到端：Ctrl+L 清空后用 Ctrl+Z 真实快捷键整批恢复。"""
        from PySide6.QtGui import QShortcut
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        win = self._win()
        win.show()
        scene = win.board.scene()
        scene.add_stroke(Stroke(QColor("black"), 4, [QPointF(0, 0), QPointF(5, 5)]))
        self.assertEqual(scene.count(), 1)

        clear_sc = next(s for s in win.findChildren(QShortcut)
                        if s.key().toString() == "Ctrl+L")
        clear_sc.activated.emit()
        self.assertEqual(scene.count(), 0)

        undo_sc = next(s for s in win.findChildren(QShortcut)
                       if s.key().toString() == "Ctrl+Z")
        undo_sc.activated.emit()
        self.assertEqual(scene.count(), 1)            # 整批恢复
        win.close()

    def test_load_is_clean_replace(self):
        """载入是显式替换：覆盖旧内容且不遗留「可撤销清空」快照（避免两步撤销）。

        载入已有原子性保护（损坏文件不丢盘），此处仅确认成功载入后：
        旧笔迹被替换、_cleared 快照被清空（undo 不会离奇还原旧板面）。
        """
        self._app()
        scene = WhiteboardScene()
        scene.add_stroke(Stroke(QColor("black"), 4, [QPointF(0, 0), QPointF(5, 5)]))
        self.assertEqual(scene.count(), 1)

        scene.load_dict({"version": 1, "strokes": [
            {"color": "#E53935", "width": 6, "points": [[1, 1], [9, 9]]}]})
        self.assertEqual(scene.count(), 1)
        self.assertEqual(scene._strokes[0].color.name().upper(), "#E53935")
        # 关键不变量：载入作为干净基线，不应遗留可撤销清空的快照
        self.assertEqual(scene._cleared, [])
        self.assertTrue(scene.can_undo())    # 载入的 1 条笔迹本身可逐条撤销
        self.assertFalse(scene.can_redo())

        # 撤销按逐条作用于载入的笔迹（而非还原旧板面）
        self.assertTrue(scene.undo())
        self.assertEqual(scene.count(), 0)
        self.assertFalse(scene.can_undo())


if __name__ == "__main__":
    prog = unittest.main(verbosity=2, exit=False)
    # 测试已产出结论；在解释器终析前主动清理并以正确退出码结束，
    # 规避 PySide6 终析阶段的 bool_dealloc 崩溃（SIGABRT）。
    _cleanup_qt()
    os._exit(0 if prog.result.wasSuccessful() else 1)

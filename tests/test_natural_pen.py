"""智学白板 SmartBoard —— 自然笔（速度感知变宽）headless 测试。

运行：QT_QPA_PLATFORM=offscreen python3 tests/test_natural_pen.py

覆盖：
- 速度→半宽映射（慢笔粗、快笔细）的单调性与取值区间（纯函数，无需 GUI）；
- 变宽轮廓路径构造（单点圆点 / 多点闭合轮廓 / 长度失配兜底）；
- Stroke 序列化对半宽的存取与老文件（无 half_widths）向后兼容；
- 场景 _attach 在变宽/固定宽度/橡皮擦三种路径下选用正确的画刷与画笔；
- 变宽笔迹的 PNG 导出不崩溃且产出非空文件；
- 工具栏「自然笔」开关与状态栏联动。

全部默认关闭自然笔，因此不改动任何既有默认书写行为（零回归）。
"""

import os
import sys
import tempfile
import unittest

# 必须在 import PySide6 之前设定 offscreen 平台
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import Qt, QPointF  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from smartboard.whiteboard import (  # noqa: E402
    Stroke,
    WhiteboardScene,
    compute_half_widths,
    build_variable_width_path,
)
from smartboard.view import WhiteboardView  # noqa: E402
from smartboard.main_window import MainWindow  # noqa: E402

# 与 smoke 测试一致的全局唯一 QApplication，规避 offscreen 下构造 QGraphicsScene 段错误
_QAPP = QApplication.instance() or QApplication([])

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


def _line(n, dx, dy=0.0):
    """生成 n 个点（沿 x 方向等距 dx）与其对应时间戳（由 caller 提供）。"""
    return [QPointF(i * dx, i * dy) for i in range(n)]


class ComputeHalfWidthTest(unittest.TestCase):
    def test_slow_is_thicker_than_fast(self):
        """核心：同样几何形状，书写越慢半宽越大（慢笔粗、快笔细）。"""
        n = 30
        pts = _line(n, 2.0)
        slow_times = [i * 20 for i in range(n)]   # 每点间隔 20ms（慢）
        fast_times = [i * 1 for i in range(n)]    # 每点间隔 1ms（快）
        base = 20
        hw_slow = compute_half_widths(pts, slow_times, base)
        hw_fast = compute_half_widths(pts, fast_times, base)
        self.assertEqual(len(hw_slow), n)
        self.assertEqual(len(hw_fast), n)
        # 落笔首点速度为 0 → 取到全局最大半宽（约等于 base/2；端点经滑动平均略降，放宽容差）
        self.assertAlmostEqual(max(hw_slow), base / 2.0, delta=0.3)
        self.assertEqual(hw_slow[0], max(hw_slow))
        # 整体上慢笔比快笔粗
        self.assertGreater(sum(hw_slow) / n, sum(hw_fast) / n)

    def test_widths_within_factor_bounds(self):
        """半宽必须落在 [min_factor, max_factor] * base/2 区间内。"""
        n = 20
        pts = _line(n, 1.0)
        times = [i * 5 for i in range(n)]
        base = 20
        hw = compute_half_widths(pts, times, base, min_factor=0.45, max_factor=1.0)
        lo, hi = 0.45 * base / 2.0, 1.0 * base / 2.0
        for h in hw:
            self.assertGreaterEqual(h, lo - 1e-9)
            self.assertLessEqual(h, hi + 1e-9)

    def test_empty_returns_empty(self):
        self.assertEqual(compute_half_widths([], [], 10), [])

    def test_constant_speed_is_fairly_uniform(self):
        """匀速书写时各点半宽应大致一致（无明显抖动）。"""
        n = 15
        pts = _line(n, 1.0)
        times = [i * 10 for i in range(n)]   # 匀速
        hw = compute_half_widths(pts, times, 16)
        spread = max(hw) - min(hw)
        self.assertLess(spread, 2.0)


class VariableWidthPathTest(unittest.TestCase):
    def test_single_point_is_dot(self):
        """单点（落笔点）应生成非空填充圆点。"""
        pts = [QPointF(5, 5)]
        hw = [4.0]
        path = build_variable_width_path(pts, hw)
        self.assertGreater(path.elementCount(), 0)
        # 圆点包围盒直径应约为 2*半宽
        b = path.boundingRect()
        self.assertAlmostEqual(b.width(), 8.0, delta=1e-3)
        self.assertAlmostEqual(b.height(), 8.0, delta=1e-3)

    def test_multi_point_closed_outline(self):
        """多点应生成闭合、非空的变宽轮廓。"""
        pts = _line(10, 3.0)
        hw = [3.0] * len(pts)
        path = build_variable_width_path(pts, hw)
        self.assertGreater(path.elementCount(), 0)
        self.assertTrue(path.boundingRect().width() > 0)

    def test_length_mismatch_falls_back(self):
        """中心线与半宽长度不一致时安全退化为折线，不抛异常。"""
        pts = _line(5, 2.0)
        hw = [3.0] * 3   # 长度不匹配
        path = build_variable_width_path(pts, hw)
        self.assertIsNotNone(path)


class StrokeRoundTripTest(unittest.TestCase):
    def test_widths_roundtrip(self):
        """含 half_widths 的笔迹可正确序列化与反序列化。"""
        pts = _line(8, 2.0)
        s = Stroke(QColor("#1E88E5"), 6, pts, half_widths=[3.0] * len(pts))
        self.assertTrue(s.uses_variable_width())
        d = s.to_dict()
        self.assertIn("half_widths", d)
        s2 = Stroke.from_dict(d)
        self.assertTrue(s2.uses_variable_width())
        self.assertEqual(len(s2._half_widths), len(pts))

    def test_backward_compat_no_widths(self):
        """老格式（无 half_widths）载入后为固定宽度笔迹，向后兼容。"""
        pts = _line(8, 2.0)
        s = Stroke(QColor("#1A1A1A"), 4, pts)
        self.assertFalse(s.uses_variable_width())
        d = s.to_dict()
        self.assertNotIn("half_widths", d)
        s2 = Stroke.from_dict(d)
        self.assertIsNone(s2._half_widths)
        self.assertFalse(s2.uses_variable_width())


class AttachRenderTest(unittest.TestCase):
    def test_variable_width_uses_brush_no_pen(self):
        """变宽笔迹用画刷填充、无描边（确保填充轮廓渲染）。"""
        pts = _line(8, 2.0)
        scene = WhiteboardScene()
        s = Stroke(QColor("#E53935"), 5, pts, half_widths=[3.0] * len(pts))
        scene.add_stroke(s)
        item = scene._items[s]
        self.assertEqual(item.pen().style(), Qt.NoPen)
        self.assertEqual(item.brush().color().name(), "#e53935")

    def test_fixed_width_uses_pen(self):
        """固定宽度笔迹仍用恒定宽度 QPen（既有行为不变）。"""
        pts = _line(8, 2.0)
        scene = WhiteboardScene()
        s = Stroke(QColor("#1A1A1A"), 4, pts)
        scene.add_stroke(s)
        item = scene._items[s]
        self.assertNotEqual(item.pen().style(), Qt.NoPen)
        self.assertEqual(item.brush().style(), Qt.NoBrush)

    def test_eraser_variable_width_uses_background(self):
        """变宽橡皮擦以背景色填充，等价的"擦除"语义。"""
        pts = _line(8, 2.0)
        scene = WhiteboardScene(background="#FFFFFF")
        s = Stroke(QColor("#E53935"), 5, pts, eraser=True, half_widths=[3.0] * len(pts))
        scene.add_stroke(s)
        item = scene._items[s]
        self.assertEqual(item.pen().style(), Qt.NoPen)
        self.assertEqual(item.brush().color().name(), "#ffffff")


class ExportVariableWidthTest(unittest.TestCase):
    def test_export_png_nonempty(self):
        """变宽笔迹导出 PNG 不崩溃且产出非空文件（默认关闭下亦可手动构造）。"""
        view = WhiteboardView()
        pts = _line(20, 3.0)
        s = Stroke(QColor("#1A1A1A"), 20, pts, half_widths=[10.0] * len(pts))
        view.scene().add_stroke(s)
        fd, path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            ok = view.export_image(path, "PNG")
            self.assertTrue(ok)
            self.assertGreater(os.path.getsize(path), 0)
        finally:
            if os.path.exists(path):
                os.remove(path)


class NaturalPenToggleTest(unittest.TestCase):
    def test_view_default_off(self):
        """默认关闭自然笔，保证既有书写行为零回归。"""
        view = WhiteboardView()
        self.assertFalse(view.variable_width)
        view.set_variable_width(True)
        self.assertTrue(view.variable_width)

    def test_mainwindow_toggle_and_status(self):
        """工具栏「自然笔」开关生效且状态栏同步。"""
        win = MainWindow()
        self.assertTrue(hasattr(win, "_natural_act"))
        win._toggle_natural(True)
        self.assertTrue(win.board.variable_width)
        self.assertIn("自然笔 开", win._status_label.text())
        win._toggle_natural(False)
        self.assertFalse(win.board.variable_width)
        self.assertIn("自然笔 关", win._status_label.text())

    def test_commit_variable_width_stroke(self):
        """端到端：以带时间戳的采样点提交，自然笔应产出变宽笔迹（点—半宽一一对应）。"""
        view = WhiteboardView()
        view.set_variable_width(True)
        pts = [QPointF(i * 2.0, i * 0.5) for i in range(20)]
        times = [i * 5 for i in range(20)]   # 匀速、时间等距
        s = Stroke(view.pen_color, view.pen_width, pts)
        s._times = times
        view._current = s
        view._commit_current()
        self.assertEqual(view.scene().count(), 1)
        committed = view.scene()._strokes[-1]
        self.assertTrue(committed.uses_variable_width())
        self.assertEqual(len(committed._half_widths), len(committed.points))
        item = view.scene()._items[committed]
        self.assertEqual(item.pen().style(), Qt.NoPen)


if __name__ == "__main__":
    unittest.main()

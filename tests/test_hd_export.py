"""智学白板 SmartBoard —— 高清导出 + 荧光笔/橡皮光标环一致性单元测试。

运行：QT_QPA_PLATFORM=offscreen python3.11 tests/test_hd_export.py

覆盖两部分（均为今天体检后新增的改进）：
1) export_image 的分辨率倍率 scale：默认 1.0 完全兼容旧行为；2×/3× 输出
   对应像素尺寸的合法 PNG；非法值安全回退；高清图确实含笔迹与背景；
   主窗口「高清导出」入口已接入且调用 2× 倍率。
2) 回归：从橡皮擦切到荧光笔再回到画笔后，画笔预览环恢复为实心画笔样式
   （修复 set_highlighter 未同步光标环导致残留橡皮红色虚线的问题）。
"""

import os
import sys
import math
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtGui import QColor, QImage, QAction  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from smartboard.whiteboard import Stroke  # noqa: E402
from smartboard.view import WhiteboardView  # noqa: E402
from smartboard.main_window import MainWindow  # noqa: E402

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


def _tmp_png():
    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    return path


class HdExportTest(unittest.TestCase):
    """验证 export_image(scale=...) 的高清导出行为。"""

    @staticmethod
    def _app():
        return QApplication.instance() or QApplication([])

    def _view_with_stroke(self):
        self._app()
        v = WhiteboardView()
        v.scene().add_stroke(
            Stroke(QColor("#1A1A1A"), 6, [QPointF(0, 0), QPointF(100, 50)])
        )
        return v

    def test_default_scale_matches_original_behavior(self):
        """不传 scale（或 scale=1.0）时，尺寸与 content_rect 严格一致（零回归）。"""
        v = self._view_with_stroke()
        rect = v.content_rect()
        expected_w = max(1, int(math.ceil(rect.width())))
        expected_h = max(1, int(math.ceil(rect.height())))
        for kw in ({}, {"scale": 1.0}):
            path = _tmp_png()
            try:
                self.assertTrue(v.export_image(path, "PNG", **kw))
                img = QImage(path)
                self.assertEqual(img.width(), expected_w)
                self.assertEqual(img.height(), expected_h)
            finally:
                os.remove(path)

    def test_scale_doubles_dimensions(self):
        """2× 导出：PNG 宽高应为 1× 的两倍（取整误差 ≤ 2px）。"""
        v = self._view_with_stroke()
        paths = []
        try:
            p1, p2 = _tmp_png(), _tmp_png()
            paths = [p1, p2]
            self.assertTrue(v.export_image(p1, "PNG", scale=1.0))
            self.assertTrue(v.export_image(p2, "PNG", scale=2.0))
            i1, i2 = QImage(p1), QImage(p2)
            self.assertLessEqual(abs(i2.width() - 2 * i1.width()), 2)
            self.assertLessEqual(abs(i2.height() - 2 * i1.height()), 2)
            self.assertGreater(i2.width(), i1.width())
            self.assertGreater(i2.height(), i1.height())
        finally:
            for p in paths:
                if os.path.exists(p):
                    os.remove(p)

    def test_scale_triples_dimensions(self):
        """3× 导出：PNG 宽高约为 1× 的三倍（取整误差 ≤ 3px）。"""
        v = self._view_with_stroke()
        p1, p3 = _tmp_png(), _tmp_png()
        try:
            v.export_image(p1, "PNG", scale=1.0)
            v.export_image(p3, "PNG", scale=3.0)
            i1, i3 = QImage(p1), QImage(p3)
            self.assertLessEqual(abs(i3.width() - 3 * i1.width()), 3)
            self.assertLessEqual(abs(i3.height() - 3 * i1.height()), 3)
        finally:
            os.remove(p1)
            os.remove(p3)

    def test_invalid_scale_falls_back_to_one(self):
        """非法倍率（0、负数、非数值）应安全回退为 1.0，不抛异常、尺寸不变。"""
        v = self._view_with_stroke()
        rect = v.content_rect()
        expected_w = max(1, int(math.ceil(rect.width())))
        for bad in (0, -2, "abc", None):
            path = _tmp_png()
            try:
                self.assertTrue(v.export_image(path, "PNG", scale=bad))
                self.assertEqual(QImage(path).width(), expected_w)
            finally:
                os.remove(path)

    def test_hd_image_has_ink_and_background(self):
        """高清图应同时含笔迹像素与背景色（真正重绘，而非空白拉伸）。"""
        v = self._view_with_stroke()
        path = _tmp_png()
        try:
            self.assertTrue(v.export_image(path, "PNG", scale=2.0))
            img = QImage(path)
            bg = v.scene().backgroundBrush().color()
            non_bg = 0
            step_x = max(1, img.width() // 30)
            step_y = max(1, img.height() // 30)
            for y in range(0, img.height(), step_y):
                for x in range(0, img.width(), step_x):
                    if img.pixelColor(x, y) != bg:
                        non_bg += 1
            self.assertGreater(non_bg, 0, "高清导出图应包含笔迹像素")
        finally:
            os.remove(path)

    def test_scale_does_not_alter_scene(self):
        """回归：导出缩放只影响输出图片，不改变场景笔迹数、点集与视图变换。"""
        v = self._view_with_stroke()
        s = v.scene()._strokes[0]
        pts_before = [QPointF(p) for p in s.points]
        transform_before = v.transform()
        path = _tmp_png()
        try:
            v.export_image(path, "PNG", scale=3.0)
            self.assertEqual(v.scene().count(), 1)
            self.assertEqual(len(s.points), len(pts_before))
            for a, b in zip(s.points, pts_before):
                self.assertAlmostEqual(a.x(), b.x(), delta=1e-9)
                self.assertAlmostEqual(a.y(), b.y(), delta=1e-9)
            self.assertEqual(transform_before, v.transform())
        finally:
            os.remove(path)

    def test_hd_export_action_present(self):
        """主窗口应提供「高清导出」入口。"""
        self._app()
        win = MainWindow()
        actions = {a.text() for a in win.findChildren(QAction)}
        self.assertIn("高清导出", actions)
        self.assertTrue(hasattr(win, "_export_hd"))
        win.close()

    def test_export_with_scale_forwards_parameter(self):
        """_export/_export_hd 应分别以 1.0/2.0 倍率调用底层导出。"""
        self._app()
        win = MainWindow()
        calls = []
        win.board.export_image = lambda path, fmt, scale=1.0: calls.append(scale) or True

        # 屏蔽文件对话框，直接给一个路径
        import smartboard.main_window as mw_mod
        orig = mw_mod.QFileDialog.getSaveFileName
        mw_mod.QFileDialog.getSaveFileName = staticmethod(
            lambda *a, **k: ("/tmp/_sb_export_test.png", "")
        )
        try:
            win._export()
            win._export_hd()
        finally:
            mw_mod.QFileDialog.getSaveFileName = orig
        self.assertEqual(calls, [1.0, 2.0])
        win.close()


class HighlighterCursorConsistencyTest(unittest.TestCase):
    """回归：工具切换后画笔预览环样式应始终与当前工具一致。"""

    @staticmethod
    def _app():
        return QApplication.instance() or QApplication([])

    def test_ring_restored_after_eraser_to_highlighter_to_pen(self):
        """橡皮擦 → 荧光笔 → 关闭荧光笔：环应恢复画笔实心样式与画笔颜色。"""
        self._app()
        v = WhiteboardView()
        v.resize(600, 400)
        v.show()
        v.set_pen_width(8)
        v._mouse_inside = True

        v.set_eraser(True)
        v._sync_cursor()
        self.assertTrue(v.pen_cursor.is_eraser_style())       # 橡皮红色虚线环

        v.set_highlighter(True)                               # 切到荧光笔（内部退出橡皮）
        self.assertFalse(v.eraser)
        self.assertTrue(v.highlighter)
        self.assertFalse(v.pen_cursor.isVisible())            # 荧光笔隐藏环

        v.set_highlighter(False)                              # 回到画笔
        self.assertFalse(v.highlighter)
        self.assertTrue(v.pen_cursor.isVisible())
        self.assertFalse(v.pen_cursor.is_eraser_style(),      # 关键：不再残留橡皮虚线样式
                         "回到画笔后光标环仍为橡皮红色虚线样式")
        self.assertAlmostEqual(v.pen_cursor.diameter(), 8.0, delta=1e-9)
        self.assertEqual(v.pen_cursor.pen_color().name().upper(),
                         v.pen_color.name().upper())

    def test_pen_direct_highlighter_roundtrip_keeps_solid_ring(self):
        """画笔 → 荧光笔 → 画笔：环始终为画笔实心样式（不引入回归）。"""
        self._app()
        v = WhiteboardView()
        v.resize(600, 400)
        v.show()
        v.set_pen_color("#1E88E5")
        v._mouse_inside = True

        v.set_highlighter(True)
        v.set_highlighter(False)
        self.assertFalse(v.pen_cursor.is_eraser_style())
        self.assertEqual(v.pen_cursor.pen_color().name().upper(), "#1E88E5")


if __name__ == "__main__":
    prog = unittest.main(verbosity=2, exit=False)
    _cleanup_qt()
    os._exit(0 if prog.result.wasSuccessful() else 1)

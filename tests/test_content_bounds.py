"""智学白板 SmartBoard —— 可见内容边界单元测试（QT_QPA_PLATFORM=offscreen）。

覆盖改进：「适应内容 / 导出图片」只根据真实板书计算内容边界，
橡皮擦笔迹（以背景色覆盖、不产生可见墨迹）不得撑大画面范围。

运行：QT_QPA_PLATFORM=offscreen python3.11 tests/test_content_bounds.py
"""

import os
import sys
import math
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from smartboard.whiteboard import Stroke, WhiteboardScene  # noqa: E402
from smartboard.view import WhiteboardView  # noqa: E402

_QAPP = QApplication.instance() or QApplication([])

import atexit  # noqa: E402
import gc  # noqa: E402


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


def _stroke(x1, y1, x2, y2, color="#1A1A1A", width=4, eraser=False):
    return Stroke(QColor(color), width, [QPointF(x1, y1), QPointF(x2, y2)],
                  eraser=eraser)


class TestContentBoundingRect(unittest.TestCase):
    """WhiteboardScene.content_bounding_rect()：只聚合非橡皮笔迹。"""

    def test_empty_scene_returns_null_rect(self):
        self.assertTrue(WhiteboardScene().content_bounding_rect().isEmpty())

    def test_only_real_strokes_bounded(self):
        s = WhiteboardScene()
        s.add_stroke(_stroke(0, 0, 100, 50))
        r = s.content_bounding_rect()
        self.assertFalse(r.isEmpty())
        self.assertLessEqual(r.left(), 0)
        self.assertGreaterEqual(r.right(), 100)

    def test_distant_eraser_excluded(self):
        """远离板书的橡皮擦不得进入内容边界（核心缺陷回归）。"""
        s = WhiteboardScene()
        s.add_stroke(_stroke(0, 0, 100, 50))
        before = s.content_bounding_rect()
        s.add_stroke(_stroke(2000, 2000, 2100, 2100, color="#FFFFFF",
                             width=40, eraser=True))
        after = s.content_bounding_rect()
        # 加入远处橡皮擦后，可见内容边界应完全不变
        self.assertAlmostEqual(before.left(), after.left(), places=3)
        self.assertAlmostEqual(before.right(), after.right(), places=3)
        self.assertAlmostEqual(before.top(), after.top(), places=3)
        self.assertAlmostEqual(before.bottom(), after.bottom(), places=3)
        self.assertLess(after.right(), 200)

    def test_only_erasers_returns_null_rect(self):
        """整板只有橡皮擦时，可见内容边界为空。"""
        s = WhiteboardScene()
        s.add_stroke(_stroke(3000, 3000, 3100, 3100, color="#FFFFFF",
                             width=40, eraser=True))
        self.assertTrue(s.content_bounding_rect().isEmpty())

    def test_mixed_uses_only_real_strokes(self):
        """混合场景下，边界恰好覆盖所有真实笔迹、完全忽略橡皮擦。"""
        s = WhiteboardScene()
        s.add_stroke(_stroke(0, 0, 50, 50))
        s.add_stroke(_stroke(200, 100, 300, 150))
        s.add_stroke(_stroke(-500, -500, -400, -400, color="#FFFFFF",
                             width=40, eraser=True))
        r = s.content_bounding_rect()
        self.assertGreaterEqual(r.right(), 300)
        self.assertLessEqual(r.left(), 0)
        self.assertGreater(r.left(), -400)  # 未被橡皮擦拉到 -500

    def test_matches_old_behavior_without_eraser(self):
        """无橡皮擦时，新方法与 itemsBoundingRect() 口径一致（零侵入）。"""
        s = WhiteboardScene()
        s.add_stroke(_stroke(10, 20, 130, 80, width=6))
        s.add_stroke(_stroke(50, 5, 60, 200, color="#E53935"))
        a = s.content_bounding_rect()
        b = s.itemsBoundingRect()
        self.assertAlmostEqual(a.left(), b.left(), places=3)
        self.assertAlmostEqual(a.right(), b.right(), places=3)
        self.assertAlmostEqual(a.top(), b.top(), places=3)
        self.assertAlmostEqual(a.bottom(), b.bottom(), places=3)


class TestViewContentRect(unittest.TestCase):
    """WhiteboardView.content_rect()：回退逻辑与导出尺寸。"""

    def test_padding_applied(self):
        s = WhiteboardScene()
        s.add_stroke(_stroke(0, 0, 100, 50))
        v = WhiteboardView(s)
        cbr = s.content_bounding_rect()
        r = v.content_rect(padding=24.0)
        self.assertAlmostEqual(r.left(), cbr.left() - 24.0, places=3)
        self.assertAlmostEqual(r.right(), cbr.right() + 24.0, places=3)

    def test_distant_eraser_not_in_content_rect(self):
        s = WhiteboardScene()
        s.add_stroke(_stroke(0, 0, 100, 50))
        v = WhiteboardView(s)
        before = v.content_rect()
        s.add_stroke(_stroke(2000, 2000, 2100, 2100, color="#FFFFFF",
                             width=40, eraser=True))
        after = v.content_rect()
        self.assertAlmostEqual(before.width(), after.width(), places=3)
        self.assertAlmostEqual(before.height(), after.height(), places=3)

    def test_only_erasers_falls_back_to_default(self):
        s = WhiteboardScene()
        s.add_stroke(_stroke(3000, 3000, 3100, 3100, color="#FFFFFF",
                             width=40, eraser=True))
        r = WhiteboardView(s).content_rect()
        self.assertAlmostEqual(r.x(), -100.0, places=3)
        self.assertAlmostEqual(r.y(), -100.0, places=3)
        self.assertAlmostEqual(r.width(), 200.0, places=3)
        self.assertAlmostEqual(r.height(), 200.0, places=3)

    def test_export_size_ignores_distant_eraser(self):
        """导出图片尺寸应只反映可见板书（+留白），不受远处橡皮擦影响。"""
        s = WhiteboardScene()
        s.add_stroke(_stroke(0, 0, 100, 50))
        v = WhiteboardView(s)
        r = v.content_rect()
        expected = (max(1, int(math.ceil(r.width()))),
                    max(1, int(math.ceil(r.height()))))
        s.add_stroke(_stroke(5000, 5000, 5100, 5100, color="#FFFFFF",
                             width=40, eraser=True))
        path = os.path.join(tempfile.gettempdir(), "sb_bounds_export.png")
        try:
            self.assertTrue(v.export_image(path, "PNG"))
            img = QImage(path)
            self.assertEqual((img.width(), img.height()), expected)
        finally:
            if os.path.exists(path):
                os.remove(path)


class TestFitToContent(unittest.TestCase):
    """fit_to_content()：无可见板书时退化为复位。"""

    def test_only_erasers_resets_view(self):
        s = WhiteboardScene()
        s.add_stroke(_stroke(3000, 3000, 3100, 3100, color="#FFFFFF",
                             width=40, eraser=True))
        v = WhiteboardView(s)
        v.zoomBy(3.0)  # 先放大
        v.fit_to_content()
        self.assertAlmostEqual(v.transform().m11(), 1.0, places=3)


if __name__ == "__main__":
    unittest.main(verbosity=2)

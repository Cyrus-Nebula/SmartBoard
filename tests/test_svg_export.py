"""智学白板 SmartBoard —— SVG 矢量导出单元测试。

运行：QT_QPA_PLATFORM=offscreen python3 tests/test_svg_export.py

覆盖：
1) 纯逻辑 scene_to_svg：root/viewBox/背景矩形、固定宽度平滑路径、荧光笔透明度、
   变宽笔迹填充轮廓、橡皮擦背景色覆盖、绘制顺序、空板回退、padding/非法值；
2) 视图 export_svg：写文件往返、合法 SVG、空板导出、零侵入场景；
3) 主窗口入口：工具栏「导出 SVG」动作已接入；
4) 零回归：导出前后场景笔迹/序列化不变。
"""

import os
import sys
import re
import tempfile
import unittest
import xml.etree.ElementTree as ET

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from smartboard.whiteboard import WhiteboardScene, Stroke, compute_half_widths  # noqa: E402
from smartboard.view import WhiteboardView  # noqa: E402
from smartboard.main_window import MainWindow  # noqa: E402
from smartboard.svg_export import scene_to_svg, path_to_d  # noqa: E402

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

SVG_NS = "{http://www.w3.org/2000/svg}"


def _parse(svg_text):
    return ET.fromstring(svg_text)


def _paths(root):
    return root.findall(SVG_NS + "path")


class SvgLogicTest(unittest.TestCase):
    """纯逻辑层：scene_to_svg 的文档结构与各笔迹语义。"""

    def test_root_viewbox_and_background(self):
        s = WhiteboardScene(background="#FAFAFA")
        s.add_stroke(Stroke(QColor("#000000"), 4, [QPointF(0, 0), QPointF(100, 50)]))
        root = _parse(scene_to_svg(s, padding=10))
        self.assertEqual(root.tag, SVG_NS + "svg")
        # viewBox = 场景内容边界（含笔宽扩张，与 PNG 导出同口径）+ 每边 10 padding
        rect = s.content_bounding_rect()
        vb = [float(v) for v in root.attrib["viewBox"].split()]
        self.assertAlmostEqual(vb[0], rect.x() - 10, places=1)
        self.assertAlmostEqual(vb[1], rect.y() - 10, places=1)
        self.assertAlmostEqual(vb[2], rect.width() + 20, places=1)
        self.assertAlmostEqual(vb[3], rect.height() + 20, places=1)
        # 背景矩形使用场景背景色
        rect = root.find(SVG_NS + "rect")
        self.assertIsNotNone(rect)
        self.assertEqual(rect.attrib["fill"], "#fafafa")

    def test_background_override(self):
        s = WhiteboardScene()
        s.add_stroke(Stroke(QColor("#000000"), 4, [QPointF(0, 0), QPointF(10, 10)]))
        rect = _parse(scene_to_svg(s, background="#123456")).find(SVG_NS + "rect")
        self.assertEqual(rect.attrib["fill"], "#123456")

    def test_fixed_width_smooth_path(self):
        s = WhiteboardScene()
        s.add_stroke(Stroke(QColor("#E53935"), 6,
                            [QPointF(0, 0), QPointF(50, 20), QPointF(100, 0)]))
        p = _paths(_parse(scene_to_svg(s)))[0]
        self.assertEqual(p.attrib["stroke"], "#e53935")
        self.assertEqual(p.attrib["stroke-width"], "6")
        self.assertEqual(p.attrib["fill"], "none")
        self.assertIn("C", p.attrib["d"])   # 平滑算法产出三次贝塞尔
        self.assertNotIn("stroke-opacity", p.attrib)

    def test_highlighter_opacity(self):
        s = WhiteboardScene()
        s.add_stroke(Stroke(QColor("#1E88E5"), 8,
                            [QPointF(0, 0), QPointF(100, 0)], highlighter=True))
        p = _paths(_parse(scene_to_svg(s)))[0]
        self.assertAlmostEqual(float(p.attrib["stroke-opacity"]), 0.4, places=2)

    def test_variable_width_filled_outline(self):
        s = WhiteboardScene()
        pts = [QPointF(0, 0), QPointF(50, 10), QPointF(100, 0)]
        hw = compute_half_widths(pts, [0, 100, 200], 10)
        s.add_stroke(Stroke(QColor("#222222"), 10, pts, half_widths=hw))
        p = _paths(_parse(scene_to_svg(s)))[0]
        self.assertEqual(p.attrib["fill"], "#222222")
        self.assertEqual(p.attrib["stroke"], "none")
        self.assertTrue(p.attrib["d"].rstrip().endswith("Z"))

    def test_eraser_uses_background_color(self):
        s = WhiteboardScene(background="#FFFFFF")
        s.add_stroke(Stroke(QColor("#000000"), 12,
                            [QPointF(0, 0), QPointF(20, 0)], eraser=True))
        p = _paths(_parse(scene_to_svg(s)))[0]
        self.assertEqual(p.attrib["stroke"], "#ffffff")
        self.assertNotIn("stroke-opacity", p.attrib)

    def test_stroke_order_preserved(self):
        s = WhiteboardScene()
        s.add_stroke(Stroke(QColor("#FF0000"), 4, [QPointF(0, 0), QPointF(10, 0)]))
        s.add_stroke(Stroke(QColor("#00FF00"), 4, [QPointF(0, 10), QPointF(10, 10)]))
        s.add_stroke(Stroke(QColor("#0000FF"), 4, [QPointF(0, 20), QPointF(10, 20)]))
        colors = [p.attrib["stroke"] for p in _paths(_parse(scene_to_svg(s)))]
        self.assertEqual(colors, ["#ff0000", "#00ff00", "#0000ff"])

    def test_empty_board_fallback(self):
        s = WhiteboardScene()
        root = _parse(scene_to_svg(s))
        vb = [float(v) for v in root.attrib["viewBox"].split()]
        # 空板回退 (-100,-100,200,200) + 默认 padding 24 => (-124,-124,248,248)
        self.assertEqual(vb, [-124.0, -124.0, 248.0, 248.0])
        self.assertEqual(len(_paths(root)), 0)

    def test_zero_padding_and_invalid_padding(self):
        s = WhiteboardScene()
        s.add_stroke(Stroke(QColor("#000000"), 4, [QPointF(0, 0), QPointF(10, 10)]))
        rect = s.content_bounding_rect()
        expected = [rect.x(), rect.y(), rect.width(), rect.height()]
        vb0 = [float(v) for v in _parse(scene_to_svg(s, padding=0)).attrib["viewBox"].split()]
        for got, exp in zip(vb0, expected):
            self.assertAlmostEqual(got, exp, places=1)
        vbneg = [float(v) for v in
                 _parse(scene_to_svg(s, padding=-5)).attrib["viewBox"].split()]
        self.assertEqual(vbneg, vb0)        # 负 padding 回退为 0
        vb24 = [float(v) for v in
                _parse(scene_to_svg(s, padding=24)).attrib["viewBox"].split()]
        vbinvalid = [float(v) for v in
                     _parse(scene_to_svg(s, padding="bad")).attrib["viewBox"].split()]
        self.assertEqual(vbinvalid, vb24)   # 非法 padding 回退为默认 24

    def test_is_well_formed_xml(self):
        s = WhiteboardScene()
        s.add_stroke(Stroke(QColor("#000000"), 4, [QPointF(0, 0), QPointF(10, 10)]))
        text = scene_to_svg(s)
        self.assertTrue(text.startswith('<?xml'))
        _parse(text)  # 不抛异常即合法


class PathToDTest(unittest.TestCase):
    """path_to_d 的数值稳定性。"""

    def test_single_and_two_points(self):
        s = WhiteboardScene()
        s.add_stroke(Stroke(QColor("#000000"), 4, [QPointF(1, 2)]))
        s.add_stroke(Stroke(QColor("#000000"), 4, [QPointF(0, 0), QPointF(10, 5)]))
        ds = [p.attrib["d"] for p in _paths(_parse(scene_to_svg(s)))]
        self.assertTrue(ds[0].startswith("M 1 2"))
        self.assertEqual(ds[1], "M 0 0 L 10 5")


class SvgViewTest(unittest.TestCase):
    """视图层：export_svg 写文件与零侵入。"""

    def setUp(self):
        self.tmp = tempfile.mktemp(suffix=".svg")

    def tearDown(self):
        if os.path.exists(self.tmp):
            os.remove(self.tmp)

    def test_export_svg_file_roundtrip(self):
        v = WhiteboardView()
        v.scene().add_stroke(Stroke(QColor("#E53935"), 6,
                                    [QPointF(0, 0), QPointF(100, 50)]))
        self.assertTrue(v.export_svg(self.tmp))
        with open(self.tmp, "r", encoding="utf-8") as f:
            text = f.read()
        root = _parse(text)
        self.assertEqual(len(_paths(root)), 1)

    def test_export_svg_empty_board(self):
        v = WhiteboardView()
        self.assertTrue(v.export_svg(self.tmp))
        with open(self.tmp, "r", encoding="utf-8") as f:
            root = _parse(f.read())
        self.assertEqual(len(_paths(root)), 0)

    def test_export_svg_does_not_mutate_scene(self):
        v = WhiteboardView()
        v.scene().add_stroke(Stroke(QColor("#000000"), 4,
                                    [QPointF(0, 0), QPointF(10, 10)]))
        before = v.scene().to_json()
        v.export_svg(self.tmp)
        self.assertEqual(v.scene().to_json(), before)   # 序列化零侵入
        self.assertEqual(v.scene().count(), 1)


class SvgMainWindowTest(unittest.TestCase):
    """主窗口：工具栏入口已接入。"""

    def test_export_svg_action_exists(self):
        w = MainWindow()
        self.assertIsNotNone(getattr(w, "_export_svg_act", None))
        self.assertTrue(callable(w._export_svg))
        w.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)

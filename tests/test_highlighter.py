"""智学白板 SmartBoard —— 荧光笔（Highlighter）工具 headless 测试。

运行：QT_QPA_PLATFORM=offscreen python3 tests/test_highlighter.py

覆盖：
- Stroke 的 highlighter 标志序列化往返，以及对老文件（无 highlighter 字段）向后兼容；
- 场景 _attach 对荧光笔做半透明渲染（HL_ALPHA）、保留真实颜色、且不被当作橡皮擦；
- 自然笔（变宽）+ 荧光笔组合时同样半透明；
- 视图工具状态：默认关闭、与橡皮擦互斥、预览半透明、预览环在荧光笔模式隐藏；
- 主窗口工具栏开关 / 状态栏标签 / 快捷键 H 联动；选色保留荧光笔（颜色即高亮色）；
- 端到端提交一条荧光笔笔迹，渲染透明度符合预期。

荧光笔默认关闭，对既有书写、橡皮擦、序列化与导出零侵入（无回归）。
"""

import os
import sys
import unittest

# 必须在 import PySide6 之前设定 offscreen 平台
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import Qt, QPointF  # noqa: E402
from PySide6.QtGui import QColor, QShortcut, QAction  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from smartboard.whiteboard import (  # noqa: E402
    Stroke, WhiteboardScene, HL_ALPHA,
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


class HighlighterStrokeTest(unittest.TestCase):
    """Stroke 的 highlighter 标志：序列化往返 + 老文件向后兼容。"""

    def test_highlighter_roundtrip(self):
        s = Stroke(QColor("#FB8C00"), 12, [QPointF(0, 0), QPointF(10, 10)],
                   highlighter=True)
        self.assertTrue(s.highlighter)
        d = s.to_dict()
        self.assertIn("highlighter", d)
        self.assertTrue(d["highlighter"])
        s2 = Stroke.from_dict(d)
        self.assertTrue(s2.highlighter)
        self.assertEqual(s2.color.name().upper(), "#FB8C00")

    def test_highlighter_omitted_when_off(self):
        """未开启荧光笔时不应写入 highlighter 字段（兼容旧文件 / 减小体积）。"""
        s = Stroke(QColor("#1A1A1A"), 4, [QPointF(0, 0)])
        d = s.to_dict()
        self.assertNotIn("highlighter", d)

    def test_backward_compat_no_field(self):
        """老格式（无 highlighter 字段）载入后为普通笔迹（非荧光笔）。"""
        d = {"color": "#1A1A1A", "width": 4, "points": [[0, 0], [10, 10]]}
        s = Stroke.from_dict(d)
        self.assertFalse(s.highlighter)
        self.assertFalse(s.eraser)

    def test_highlighter_and_eraser_independent(self):
        """highlighter 与 eraser 是独立布尔，可同时为假；荧光笔不是橡皮擦。"""
        s = Stroke(QColor("#1A1A1A"), 4, [QPointF(0, 0)], highlighter=True)
        self.assertTrue(s.highlighter)
        self.assertFalse(s.eraser)


class HighlighterAttachTest(unittest.TestCase):
    """场景 _attach：荧光笔半透明渲染、保留真实颜色、且非橡皮擦语义。"""

    def test_fixed_width_highlighter_translucent(self):
        scene = WhiteboardScene()
        s = Stroke(QColor("#FB8C00"), 12, [QPointF(0, 0), QPointF(20, 10)],
                   highlighter=True)
        scene.add_stroke(s)
        item = scene._items[s]
        # 半透明叠加：不透明度应等于 HL_ALPHA
        self.assertAlmostEqual(item.opacity(), HL_ALPHA, delta=1e-6)
        # 真实颜色被保留（荧光笔以自身颜色叠加，而非背景色覆盖）
        self.assertEqual(item.pen().color().name().upper(), "#FB8C00")
        # 固定宽度笔迹仍用 QPen（非 NoPen），仅降低不透明度
        self.assertNotEqual(item.pen().style(), Qt.NoPen)
        # 透明度不应被当作橡皮擦（橡皮擦以背景色覆盖、不透明）
        self.assertNotEqual(item.pen().color().name().upper(),
                            scene.backgroundBrush().color().name().upper())

    def test_variable_width_highlighter_translucent(self):
        """自然笔（变宽）+ 荧光笔：同样半透明，且填充色为真实颜色。"""
        scene = WhiteboardScene(background="#FFFFFF")
        pts = [QPointF(i * 2.0, i * 0.5) for i in range(8)]
        s = Stroke(QColor("#E53935"), 10, pts, half_widths=[5.0] * len(pts),
                   highlighter=True)
        scene.add_stroke(s)
        item = scene._items[s]
        self.assertAlmostEqual(item.opacity(), HL_ALPHA, delta=1e-6)
        self.assertEqual(item.pen().style(), Qt.NoPen)  # 变宽笔迹用填充轮廓
        self.assertEqual(item.brush().color().name().upper(), "#E53935")

    def test_eraser_never_translucent(self):
        """橡皮擦必须不透明（背景色覆盖），即便误带 highlighter 标志也不能半透明。"""
        scene = WhiteboardScene(background="#FFFFFF")
        s = Stroke(QColor("#1A1A1A"), 20, [QPointF(0, 0), QPointF(10, 10)],
                   eraser=True, highlighter=True)
        scene.add_stroke(s)
        item = scene._items[s]
        self.assertAlmostEqual(item.opacity(), 1.0, delta=1e-6)
        self.assertEqual(item.pen().color().name().upper(), "#FFFFFF")


class HighlighterViewTest(unittest.TestCase):
    """视图工具状态：默认关闭、与橡皮擦互斥、预览半透明、预览环隐藏。"""

    @staticmethod
    def _app():
        return QApplication.instance() or QApplication([])

    def test_default_off(self):
        view = WhiteboardView()
        self.assertFalse(view.highlighter)
        view.set_highlighter(True)
        self.assertTrue(view.highlighter)
        view.set_highlighter(False)
        self.assertFalse(view.highlighter)

    def test_mutual_exclusion_with_eraser(self):
        view = WhiteboardView()
        # 开橡皮擦 -> 关荧光笔
        view.set_highlighter(True)
        self.assertTrue(view.highlighter)
        view.set_eraser(True)
        self.assertTrue(view.eraser)
        self.assertFalse(view.highlighter)
        # 开荧光笔 -> 关橡皮擦
        view.set_highlighter(True)
        self.assertTrue(view.highlighter)
        self.assertFalse(view.eraser)

    def test_preview_opacity_when_highlighter(self):
        view = WhiteboardView()
        view.set_highlighter(True)
        view._current = Stroke(QColor("#FB8C00"), 12,
                               [QPointF(0, 0), QPointF(10, 10)], highlighter=True)
        view._start_preview()
        self.assertIsNotNone(view._preview)
        self.assertAlmostEqual(view._preview.opacity(), HL_ALPHA, delta=1e-6)
        view._finish_preview()

    def test_cursor_ring_hidden_when_highlighter(self):
        """荧光笔模式下预览环应隐藏（与橡皮擦一致），避免实心环误导半透明笔宽。"""
        view = WhiteboardView()
        view.show()
        view._mouse_inside = True
        view._update_cursor_visibility()          # 画笔 + 鼠标在内 -> 显示
        self.assertTrue(view.pen_cursor.isVisible())

        view.set_highlighter(True)                # 切荧光笔 -> 隐藏
        self.assertFalse(view.pen_cursor.isVisible())

        view.set_highlighter(False)               # 切回画笔 -> 显示
        self.assertTrue(view.pen_cursor.isVisible())

    def test_commit_highlighter_stroke(self):
        """端到端：以荧光笔模式提交，场景项半透明且保留 highlighter 标志。"""
        view = WhiteboardView()
        view.set_highlighter(True)
        s = Stroke(view.pen_color, view.pen_width,
                   [QPointF(0, 0), QPointF(10, 10), QPointF(20, 5)],
                   highlighter=True)
        view._current = s
        view._commit_current()
        self.assertEqual(view.scene().count(), 1)
        committed = view.scene()._strokes[-1]
        self.assertTrue(committed.highlighter)
        self.assertFalse(committed.eraser)
        item = view.scene()._items[committed]
        self.assertAlmostEqual(item.opacity(), HL_ALPHA, delta=1e-6)


class HighlighterMainWindowTest(unittest.TestCase):
    """主窗口联动：工具栏开关 / 状态栏标签 / 快捷键 H / 选色保留荧光笔。"""

    @staticmethod
    def _app():
        return QApplication.instance() or QApplication([])

    def _win(self):
        self._app()
        return MainWindow()

    def test_action_present(self):
        win = self._win()
        actions = {a.text() for a in win.findChildren(QAction)}
        self.assertIn("荧光笔", actions)
        self.assertIsNotNone(win._hl_act)
        win.close()

    def test_toggle_updates_status_and_flag(self):
        win = self._win()
        win._toggle_highlighter(True)
        self.assertTrue(win.board.highlighter)
        self.assertIn("荧光笔", win._status_label.text())
        win._toggle_highlighter(False)
        self.assertFalse(win.board.highlighter)
        win.close()

    def test_pick_color_keeps_highlighter(self):
        """选色不应退出荧光笔（颜色即高亮色）；但应退出橡皮擦。"""
        win = self._win()
        win._toggle_highlighter(True)
        self.assertTrue(win.board.highlighter)
        self.assertTrue(win._hl_act.isChecked())

        win._pick_color("#E53935")
        # 荧光笔保留、且高亮色更新为所选颜色
        self.assertTrue(win.board.highlighter)
        self.assertTrue(win._hl_act.isChecked())
        self.assertEqual(win._active_color, "#E53935")
        # 橡皮擦被退出
        self.assertFalse(win.board.eraser)
        self.assertFalse(win._eraser_act.isChecked())
        win.close()

    def test_eraser_toggle_clears_highlighter_checkbox(self):
        win = self._win()
        win._toggle_highlighter(True)
        win._toggle_eraser(True)                 # 开橡皮擦 -> 退出荧光笔
        self.assertFalse(win.board.highlighter)
        self.assertFalse(win._hl_act.isChecked())
        self.assertTrue(win._eraser_act.isChecked())
        win.close()

    def test_shortcut_h_toggles(self):
        win = self._win()
        registered = {sc.key().toString() for sc in win.findChildren(QShortcut)}
        self.assertIn("H", registered)

        hl_sc = next(s for s in win.findChildren(QShortcut)
                     if s.key().toString() == "H")
        self.assertFalse(win.board.highlighter)
        hl_sc.activated.emit()
        self.assertTrue(win.board.highlighter)
        hl_sc.activated.emit()
        self.assertFalse(win.board.highlighter)
        win.close()


if __name__ == "__main__":
    prog = unittest.main(verbosity=2, exit=False)
    _cleanup_qt()
    os._exit(0 if prog.result.wasSuccessful() else 1)

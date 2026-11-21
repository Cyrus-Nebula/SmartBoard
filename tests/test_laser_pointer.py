"""智学白板 SmartBoard —— 激光笔（演示指示点）headless 测试。

运行：QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_laser_pointer.py -q

覆盖：
- 叠加层：默认关闭 / 激活切换 / 颜色与位置 / 鼠标事件穿透 / 真实绘制出像素；
- 视图：激光模式默认关、左键不产生笔迹、关闭后正常书写（零回归）、
  光点跟随鼠标与进出显隐、右键精确擦除仍可用；
- 零侵入：激光激活不影响序列化与 PNG 导出；
- 主窗口：工具栏动作、快捷键 L、与橡皮擦/荧光笔互斥、状态栏显示。
"""

import os
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault(
    "SMARTBOARD_DRAFT_DIR", tempfile.mkdtemp(prefix="smartboard_laser_draft_")
)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import Qt, QEvent, QPointF  # noqa: E402
from PySide6.QtGui import QMouseEvent, QShortcut  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from smartboard.laser import LaserPointerOverlay, LASER_COLOR  # noqa: E402
from smartboard.view import WhiteboardView  # noqa: E402
from smartboard.main_window import MainWindow  # noqa: E402

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


def _press(view, pos, button=Qt.LeftButton):
    ev = QMouseEvent(QEvent.MouseButtonPress, QPointF(pos, pos),
                     button, button, Qt.NoModifier)
    view.mousePressEvent(ev)


def _move(view, pos, button=Qt.NoButton):
    ev = QMouseEvent(QEvent.MouseMove, QPointF(pos, pos),
                     Qt.NoButton, button, Qt.NoModifier)
    view.mouseMoveEvent(ev)


def _release(view, pos, button=Qt.LeftButton):
    ev = QMouseEvent(QEvent.MouseButtonRelease, QPointF(pos, pos),
                     button, Qt.NoButton, Qt.NoModifier)
    view.mouseReleaseEvent(ev)


class LaserOverlayTest(unittest.TestCase):
    def test_default_state(self):
        o = LaserPointerOverlay()
        self.assertFalse(o.is_active())
        self.assertFalse(o.isVisible())
        self.assertEqual(o.color().name().lower(), LASER_COLOR.lower())
        self.assertGreater(o.diameter(), 0)

    def test_active_toggle_hides_on_deactivate(self):
        o = LaserPointerOverlay()
        o.set_active(True)
        self.assertTrue(o.is_active())
        o.set_active(False)
        self.assertFalse(o.is_active())
        self.assertFalse(o.isVisible())

    def test_transparent_for_mouse(self):
        """叠加层必须鼠标事件穿透，否则会拦截书写 / 擦除 / 平移。"""
        o = LaserPointerOverlay()
        self.assertTrue(o.testAttribute(Qt.WA_TransparentForMouseEvents))
        self.assertTrue(o.testAttribute(Qt.WA_TranslucentBackground))

    def test_move_to_centers_on_position(self):
        o = LaserPointerOverlay()
        o.move_to(QPointF(60, 60))
        self.assertEqual(o.pos().x() + o.width() // 2, 60)
        self.assertEqual(o.pos().y() + o.height() // 2, 60)

    def test_paint_produces_visible_pixels(self):
        """激活后真实绘制出非透明像素（发光点），而非空 widget。"""
        o = LaserPointerOverlay()
        o.set_active(True)
        o.show()
        pm = o.grab()
        img = pm.toImage()
        nontransparent = sum(
            1 for y in range(img.height())
            for x in range(img.width())
            if img.pixelColor(x, y).alpha() > 0
        )
        self.assertGreater(nontransparent, 0)


class LaserViewTest(unittest.TestCase):
    def test_laser_mode_default_off(self):
        view = WhiteboardView()
        self.assertFalse(view.is_laser_mode())

    def test_laser_left_click_creates_no_stroke(self):
        """激光模式下完整左键流程不落笔、不留任何 Stroke。"""
        view = WhiteboardView()
        view.set_laser_mode(True)
        _press(view, 20.0)
        for x in (40.0, 60.0, 80.0):
            _move(view, x)
        _release(view, 80.0)
        self.assertEqual(view.scene().count(), 0)
        self.assertFalse(view._drawing)
        self.assertIsNone(view._current)

    def test_normal_writing_after_leaving_laser_mode(self):
        """关闭激光笔后左键书写恢复正常（零回归）。"""
        view = WhiteboardView()
        view.set_laser_mode(True)
        view.set_laser_mode(False)
        _press(view, 10.0)
        for x in (30.0, 50.0):
            _move(view, x)
        _release(view, 50.0)
        self.assertEqual(view.scene().count(), 1)

    def test_laser_follows_mouse_and_visibility(self):
        view = WhiteboardView()
        view.set_laser_mode(True)
        view._mouse_inside = True
        view._update_cursor_visibility()
        # 用 isHidden() 判断显式显隐：父 viewport 未显示时 isVisible() 恒为 False
        self.assertFalse(view._laser.isHidden())
        self.assertTrue(view.pen_cursor.isHidden())
        _move(view, 60.0)
        self.assertEqual(view._laser.pos().x() + view._laser.width() // 2, 60)
        view._mouse_inside = False
        view._update_cursor_visibility()
        self.assertTrue(view._laser.isHidden())

    def test_right_click_erase_still_works_in_laser_mode(self):
        """激光模式下右键精确擦除不受影响。"""
        view = WhiteboardView()
        _press(view, 50.0)
        _move(view, 80.0)
        _release(view, 80.0)
        self.assertEqual(view.scene().count(), 1)
        view.set_laser_mode(True)
        _press(view, 50.0, button=Qt.RightButton)
        self.assertEqual(view.scene().count(), 0)

    def test_no_impact_on_serialization_and_export(self):
        """激光点不进 scene.to_dict()；激光激活时导出仍正常。"""
        view = WhiteboardView()
        view.set_laser_mode(True)
        data = view.scene().to_dict()
        self.assertEqual(data["strokes"], [])
        self.assertNotIn("laser", data)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            path = f.name
        try:
            self.assertTrue(view.export_image(path, "PNG"))
            self.assertGreater(os.path.getsize(path), 0)
        finally:
            os.remove(path)


class LaserMainWindowTest(unittest.TestCase):
    def test_toolbar_action_and_shortcut_exist(self):
        w = MainWindow()
        self.assertIsNotNone(w._laser_act)
        self.assertEqual(w._laser_act.text(), "激光笔")
        self.assertTrue(w._laser_act.isCheckable())
        shortcuts = w.findChildren(QShortcut)
        keys = [s.key().toString() for s in shortcuts]
        self.assertIn("L", keys)
        w.close()

    def test_toggle_mutual_exclusion_and_status(self):
        w = MainWindow()
        w._toggle_eraser(True)
        self.assertTrue(w.board.eraser)
        w._toggle_highlighter(True)
        self.assertTrue(w.board.highlighter)       # 荧光与橡皮互斥后橡皮关闭
        self.assertFalse(w.board.eraser)
        w._toggle_laser(True)
        self.assertTrue(w.board.is_laser_mode())
        self.assertTrue(w._laser_act.isChecked())
        self.assertFalse(w.board.eraser)
        self.assertFalse(w.board.highlighter)
        self.assertIn("激光笔", w._status_label.text())
        w._toggle_laser(False)
        self.assertFalse(w.board.is_laser_mode())
        self.assertFalse(w._laser_act.isChecked())
        w.close()


class LaserToolMutualExclusionTest(unittest.TestCase):
    """激光笔与橡皮擦/荧光笔的「双向」互斥一致性回归。

    缺陷背景：开启激光笔会主动退出橡皮擦/荧光笔，但反向切换（激光→橡皮擦/
    荧光笔）未退出激光，导致「工具栏勾选橡皮擦、状态栏却显示激光笔、左键仍被
    激光拦截擦不掉」的状态歧义。此处覆盖反向互斥与功能生效。
    """

    def test_view_eraser_exits_laser(self):
        view = WhiteboardView()
        view.set_laser_mode(True)
        self.assertTrue(view.is_laser_mode())
        view.set_eraser(True)
        self.assertFalse(view.is_laser_mode())
        self.assertTrue(view.eraser)

    def test_view_highlighter_exits_laser(self):
        view = WhiteboardView()
        view.set_laser_mode(True)
        view.set_highlighter(True)
        self.assertFalse(view.is_laser_mode())
        self.assertTrue(view.highlighter)

    def test_toolbar_eraser_exits_laser_and_syncs(self):
        w = MainWindow()
        w._toggle_laser(True)
        w._toggle_eraser(True)
        self.assertFalse(w.board.is_laser_mode())
        self.assertTrue(w.board.eraser)
        self.assertFalse(w._laser_act.isChecked())
        self.assertTrue(w._eraser_act.isChecked())
        self.assertIn("橡皮擦", w._status_label.text())
        w.close()

    def test_toolbar_highlighter_exits_laser_and_syncs(self):
        w = MainWindow()
        w._toggle_laser(True)
        w._toggle_highlighter(True)
        self.assertFalse(w.board.is_laser_mode())
        self.assertTrue(w.board.highlighter)
        self.assertFalse(w._laser_act.isChecked())
        self.assertTrue(w._hl_act.isChecked())
        self.assertIn("荧光笔", w._status_label.text())
        w.close()

    def test_left_click_writes_again_after_leaving_laser(self):
        """功能性验证：激光→橡皮擦切换后左键恢复落笔（不再只作指示）。"""
        w = MainWindow()
        w._toggle_laser(True)
        w._toggle_eraser(True)
        self.assertFalse(w.board.is_laser_mode())
        _press(w.board, 20.0)
        _move(w.board, 40.0)
        _release(w.board, 40.0)
        # 橡皮擦模式下左键会产生一条 eraser 笔迹，证明左键不再被激光拦截
        self.assertEqual(w.board.scene().count(), 1)
        w.close()

    def test_laser_still_exits_other_tools(self):
        """零回归：原方向（激光主动退出橡皮擦/荧光笔）仍然成立。"""
        w = MainWindow()
        w._toggle_eraser(True)
        w._toggle_highlighter(True)
        w._toggle_laser(True)
        self.assertFalse(w.board.eraser)
        self.assertFalse(w.board.highlighter)
        self.assertTrue(w.board.is_laser_mode())
        w.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)

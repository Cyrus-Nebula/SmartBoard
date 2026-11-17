"""智学白板 SmartBoard —— 草稿自动保存 / 崩溃恢复 headless 测试。

运行：QT_QPA_PLATFORM=offscreen python3.11 -m pytest tests/test_autosave.py -q

覆盖两层：
1) DraftManager 纯逻辑（无 GUI）：原子落盘、读回往返、损坏/版本不符容错、清理；
2) MainWindow 集成：脏标记、定时/退出保存、空板清草稿、恢复往返与坏草稿安全。
"""

import os
import sys
import json
import atexit
import gc
import tempfile
import unittest

# 必须在 import PySide6 之前设定 offscreen 平台
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# 把默认草稿路径隔离到临时目录，避免测试污染用户 home 或互相干扰
os.environ.setdefault(
    "SMARTBOARD_DRAFT_DIR", tempfile.mkdtemp(prefix="smartboard_autosave_test_")
)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from smartboard.autosave import (  # noqa: E402
    DraftManager, DRAFT_VERSION, DRAFT_FILENAME, default_draft_path,
)
from smartboard.whiteboard import Stroke  # noqa: E402
from smartboard.main_window import MainWindow  # noqa: E402

# 全程唯一 QApplication（构造 QGraphicsScene 必须先有）
_QAPP = QApplication.instance() or QApplication([])


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


def _sample_board(n=2):
    """构造 n 条笔迹的白板 dict（结构与 scene.to_dict() 一致）。"""
    return {"version": 1, "strokes": [
        {"color": "#E53935", "width": 4, "eraser": False,
         "points": [[i, i * 2] for i in range(n + 1)]}
        for _ in range(n)
    ]}


class DraftManagerTest(unittest.TestCase):
    """草稿文件读写：纯逻辑、无 GUI、确定性验证。"""

    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="sb_draft_")
        self.path = os.path.join(self.directory, "draft.json")
        self.mgr = DraftManager(path=self.path)

    def tearDown(self):
        self.mgr.clear()
        try:
            os.rmdir(self.directory)
        except OSError:
            pass

    def test_no_draft_initially(self):
        self.assertFalse(self.mgr.has_draft())
        self.assertIsNone(self.mgr.load())
        self.assertIsNone(self.mgr.info())

    def test_save_load_roundtrip(self):
        board = _sample_board(3)
        self.assertTrue(self.mgr.save(board))
        self.assertTrue(os.path.isfile(self.path))
        self.assertEqual(self.mgr.load(), board)
        info = self.mgr.info()
        self.assertEqual(info["stroke_count"], 3)
        self.assertEqual(info["version"], DRAFT_VERSION)
        self.assertTrue(info["saved_at"])

    def test_atomic_write_leaves_no_temp(self):
        self.mgr.save(_sample_board(1))
        self.assertFalse(os.path.exists(self.path + ".tmp"))

    def test_overwrite_keeps_latest(self):
        self.mgr.save(_sample_board(1))
        self.mgr.save(_sample_board(5))
        self.assertEqual(len(self.mgr.load()["strokes"]), 5)

    def test_clear_removes_draft(self):
        self.mgr.save(_sample_board(2))
        self.assertTrue(self.mgr.has_draft())
        self.assertTrue(self.mgr.clear())
        self.assertFalse(self.mgr.has_draft())
        self.assertIsNone(self.mgr.load())
        self.assertFalse(os.path.exists(self.path))

    def test_corrupt_json_is_ignored(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("{not valid json")
        self.assertIsNone(self.mgr.load())
        self.assertIsNone(self.mgr.info())
        self.assertFalse(self.mgr.has_draft())

    def test_foreign_json_is_ignored(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump({"foo": "bar"}, fh)
        self.assertIsNone(self.mgr.load())
        self.assertFalse(self.mgr.has_draft())

    def test_wrong_version_is_ignored(self):
        payload = {"app": "SmartBoard", "version": DRAFT_VERSION + 99,
                   "saved_at": "x", "board": _sample_board(1)}
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        self.assertIsNone(self.mgr.load())
        self.assertFalse(self.mgr.has_draft())

    def test_empty_board_not_worth_restoring(self):
        self.mgr.save({"version": 1, "strokes": []})
        # 空板草稿可读取（结构有效），但不被视为「有价值的恢复点」
        self.assertIsNotNone(self.mgr.load())
        self.assertFalse(self.mgr.has_draft())

    def test_save_rejects_non_dict(self):
        self.assertFalse(self.mgr.save("bad"))
        self.assertFalse(self.mgr.save(None))
        self.assertFalse(os.path.exists(self.path))

    def test_nested_directory_auto_created(self):
        nested = os.path.join(self.directory, "a", "b", "draft.json")
        mgr = DraftManager(path=nested)
        self.assertTrue(mgr.save(_sample_board(1)))
        self.assertTrue(os.path.isfile(nested))
        self.assertEqual(len(mgr.load()["strokes"]), 1)

    def test_env_dir_overrides_default_path(self):
        """SMARTBOARD_DRAFT_DIR 应覆盖默认草稿位置（测试隔离 / 部署自定义）。"""
        old = os.environ.get("SMARTBOARD_DRAFT_DIR")
        custom = tempfile.mkdtemp(prefix="sb_env_dir_")
        try:
            os.environ["SMARTBOARD_DRAFT_DIR"] = custom
            self.assertEqual(
                default_draft_path(), os.path.join(custom, DRAFT_FILENAME)
            )
        finally:
            if old is None:
                os.environ.pop("SMARTBOARD_DRAFT_DIR", None)
            else:
                os.environ["SMARTBOARD_DRAFT_DIR"] = old
            try:
                os.rmdir(custom)
            except OSError:
                pass


class MainWindowAutosaveTest(unittest.TestCase):
    """主窗口集成：脏标记、定时/退出保存、恢复与坏草稿安全。"""

    def _win(self):
        win = MainWindow()
        # 每个用例隔离到独立草稿路径，避免相互污染
        win._drafts.path = os.path.join(
            tempfile.mkdtemp(prefix="sb_win_draft_"), "draft.json"
        )
        return win

    @staticmethod
    def _add_stroke(win):
        win.board.scene().add_stroke(
            Stroke(QColor("#1A1A1A"), 4, [QPointF(0, 0), QPointF(10, 10)])
        )

    def test_autosave_wiring_present(self):
        win = self._win()
        self.assertIsInstance(win._drafts, DraftManager)
        self.assertIsNotNone(win._draft_timer)
        self.assertTrue(win._draft_timer.isActive())
        self.assertFalse(win._draft_dirty)
        win.close()

    def test_stroke_change_marks_dirty(self):
        win = self._win()
        self.assertFalse(win._draft_dirty)
        self._add_stroke(win)
        self.assertTrue(win._draft_dirty,
                        "strokesChanged 应自动把草稿标记为脏")
        win.close()

    def test_autosave_writes_only_when_dirty(self):
        win = self._win()
        win._autosave_draft()  # 未脏：不写
        self.assertIsNone(win._drafts.load())
        self._add_stroke(win)
        win._autosave_draft()
        board = win._drafts.load()
        self.assertIsNotNone(board)
        self.assertEqual(len(board["strokes"]), 1)
        self.assertFalse(win._draft_dirty)  # 保存后复位
        win.close()

    def test_empty_board_autosave_clears_draft(self):
        win = self._win()
        win._drafts.save(_sample_board(1))  # 先放一份草稿
        win._draft_dirty = True
        win._autosave_draft()               # 场景空 -> 清除草稿
        self.assertIsNone(win._drafts.load())
        win.close()

    def test_close_event_saves_pending_draft(self):
        """退出时尚未到定时器触发的改动也应被保存（防误关丢板书）。"""
        win = self._win()
        self._add_stroke(win)
        self.assertTrue(win._draft_dirty)
        win.close()
        self.assertEqual(len(win._drafts.load()["strokes"]), 1)

    def test_restore_draft_roundtrip(self):
        win1 = self._win()
        self._add_stroke(win1)
        win1._autosave_draft()
        draft_path = win1._drafts.path
        win1.close()

        win2 = self._win()
        win2._drafts.path = draft_path
        self.assertEqual(win2.board.scene().count(), 0)
        self.assertTrue(win2.restore_draft())
        self.assertEqual(win2.board.scene().count(), 1)
        # 恢复后草稿被清除，避免下次重复提示
        self.assertFalse(win2._drafts.has_draft())
        win2.close()

    def test_restore_without_draft_returns_false(self):
        win = self._win()
        self.assertFalse(win.restore_draft())
        self.assertEqual(win.board.scene().count(), 0)
        win.close()

    def test_restore_corrupt_draft_is_safe(self):
        win = self._win()
        with open(win._drafts.path, "w", encoding="utf-8") as fh:
            fh.write("garbage")
        self.assertFalse(win.restore_draft())
        self.assertEqual(win.board.scene().count(), 0)
        # 坏草稿被清除，不反复打扰
        self.assertFalse(os.path.exists(win._drafts.path))
        win.close()


if __name__ == "__main__":
    prog = unittest.main(verbosity=2, exit=False)
    _cleanup_qt()
    os._exit(0 if prog.result.wasSuccessful() else 1)

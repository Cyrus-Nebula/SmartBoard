"""智学白板 SmartBoard —— AI 教学联动单元测试（纯逻辑 + offscreen 集成）。

运行：QT_QPA_PLATFORM=offscreen python3 tests/test_ai_link.py
"""

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from smartboard.whiteboard import WhiteboardScene, Stroke  # noqa: E402
from smartboard.ai_link import (  # noqa: E402
    AITeacher, board_dict_to_text, analyze_board,
)

_QAPP = QApplication.instance() or QApplication([])

# 退出前有序拆除 Qt 对象，规避 PySide6 终析阶段的 bool_dealloc 崩溃。
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


class TestBoardToText(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(board_dict_to_text({}), "(空白白板)")
        self.assertEqual(board_dict_to_text({"strokes": []}), "(空白白板)")
        self.assertEqual(board_dict_to_text("not a dict"), "(空白白板)")

    def test_strokes_text(self):
        board = {
            "version": 1,
            "strokes": [
                {"color": "#1A1A1A", "width": 4, "eraser": False,
                 "points": [[0, 0], [10, 10], [20, 5]]},
                {"color": "#E53935", "width": 6, "eraser": True,
                 "points": [[1, 1], [9, 9]]},
            ],
        }
        txt = board_dict_to_text(board)
        self.assertIn("第1条笔迹", txt)
        self.assertIn("第2条橡皮擦", txt)
        self.assertIn("#1A1A1A", txt)
        self.assertIn("(0,0)", txt)

    def test_long_stroke_truncated(self):
        board = {"strokes": [{
            "color": "#000", "width": 4, "points": [[i, i] for i in range(20)]}]}
        txt = board_dict_to_text(board)
        self.assertIn("...", txt)  # 长笔迹应被采样截断


class TestAITeacher(unittest.TestCase):
    def test_empty_question(self):
        self.assertEqual(AITeacher().ask("   "), "请先提出一个教学问题。")
        self.assertEqual(AITeacher().ask(""), "请先提出一个教学问题。")

    def test_local_mock_has_question_and_count(self):
        ai = AITeacher()
        board = {"strokes": [
            {"color": "#000", "width": 4, "points": [[0, 0]]},
            {"color": "#111", "width": 4, "points": [[1, 1]]},
        ]}
        ctx = board_dict_to_text(board)
        ans = ai.ask("什么是函数？", ctx)
        self.assertIn("什么是函数？", ans)
        self.assertIn("2", ans)  # 当前白板共 2 条内容

    def test_remote_endpoint_no_network(self):
        # 配置 endpoint 但不可达：应优雅降级到本地答疑，而非崩溃或返回旧桩文案
        ai = AITeacher(endpoint="http://example.invalid/api", api_key="x")
        ans = ai.ask("直角三角形边长关系？", "第1条笔迹")
        self.assertIn("已回退本地答疑", ans)            # 不可达时优雅降级
        self.assertIn("直角三角形边长关系？", ans)      # 仍含原问题
        self.assertNotIn("[远程模式已配置但未启用网络请求]", ans)


class TestAISceneIntegration(unittest.TestCase):
    def test_context_from_real_scene(self):
        scene = WhiteboardScene()
        scene.add_stroke(Stroke(QColor("#1E88E5"), 8, [QPointF(1, 1), QPointF(9, 9)]))
        scene.add_stroke(Stroke(QColor("#43A047"), 3,
                                [QPointF(2, 2), QPointF(8, 2)], eraser=True))
        ctx = board_dict_to_text(scene.to_dict())
        self.assertIn("第1条笔迹", ctx)
        self.assertIn("第2条橡皮擦", ctx)
        self.assertIn("#1e88e5", ctx)
        ans = AITeacher().ask("讲解这道题", ctx)
        self.assertIn("2", ans)


class TestRemoteBackend(unittest.TestCase):
    """验证远程后端已从 no-op 桩升级为真实 HTTP 调用（标准库 urllib）。

    用 monkeypatch 替换底层 _http_post_json，避免真实网络依赖，确定性验证：
    成功响应（JSON / 纯文本 / OpenAI 风格）能正确解析；网络失败能优雅降级。
    """

    def _ai(self, endpoint="https://api.example/ai", api_key="k"):
        return AITeacher(endpoint=endpoint, api_key=api_key)

    def test_remote_success_json_answer(self):
        from unittest import mock
        ai = self._ai()
        with mock.patch("smartboard.ai_link._http_post_json",
                        return_value='{"answer": "勾股定理：a²+b²=c²"}'):
            ans = ai.ask("直角三角形边长关系？", "第1条笔迹")
        self.assertIn("[远程 AI 答疑]", ans)
        self.assertIn("勾股定理", ans)

    def test_remote_success_plain_text(self):
        from unittest import mock
        ai = self._ai()
        with mock.patch("smartboard.ai_link._http_post_json",
                        return_value="这是纯文本答复"):
            ans = ai.ask("什么是函数？", "")
        self.assertIn("[远程 AI 答疑]", ans)
        self.assertIn("这是纯文本答复", ans)

    def test_remote_success_openai_style(self):
        from unittest import mock
        import json as _json
        payload = _json.dumps(
            {"choices": [{"message": {"content": "面积=底×高÷2"}}]})
        ai = self._ai()
        with mock.patch("smartboard.ai_link._http_post_json",
                        return_value=payload):
            ans = ai.ask("三角形面积公式？", "")
        self.assertIn("[远程 AI 答疑]", ans)
        self.assertIn("面积=底×高÷2", ans)

    def test_remote_empty_reply_falls_back(self):
        from unittest import mock
        ai = self._ai()
        with mock.patch("smartboard.ai_link._http_post_json",
                        return_value="   "):
            ans = ai.ask("题目？", "")
        self.assertIn("未返回有效答复", ans)
        self.assertIn("已回退本地答疑", ans)

    def test_remote_failure_falls_back(self):
        from unittest import mock
        ai = self._ai()
        with mock.patch("smartboard.ai_link._http_post_json",
                        side_effect=Exception("network down")):
            ans = ai.ask("题目？", "")
        self.assertIn("已回退本地答疑", ans)
        self.assertIn("network down", ans)
        self.assertIn("题目？", ans)


class TestAnalyzeBoard(unittest.TestCase):
    """验证新增的结构化白板统计（无 GUI 依赖、确定性）。"""

    def test_empty(self):
        s = analyze_board({})
        self.assertTrue(s["empty"])
        self.assertEqual(s["total"], 0)
        self.assertEqual(s["dominant_color"], None)

    def test_counts_normal_and_eraser(self):
        board = {"strokes": [
            {"color": "#1A1A1A", "width": 4, "points": [[0, 0]]},
            {"color": "#E53935", "width": 6, "eraser": False, "points": [[1, 1]]},
            {"color": "#1A1A1A", "width": 2, "eraser": True, "points": [[2, 2]]},
        ]}
        s = analyze_board(board)
        self.assertEqual(s["total"], 3)
        self.assertEqual(s["normal"], 2)
        self.assertEqual(s["eraser"], 1)
        # 仅普通笔迹计入配色：第 3 条是橡皮擦，其颜色不进入 colors
        self.assertEqual(s["colors"].get("#1a1a1a"), 1)
        self.assertEqual(s["colors"].get("#e53935"), 1)
        self.assertEqual(s["dominant_color"], "#1a1a1a")

    def test_three_digit_color_expanded(self):
        board = {"strokes": [{"color": "#FFF", "width": 4, "points": [[0, 0]]}]}
        s = analyze_board(board)
        self.assertEqual(s["colors"].get("#ffffff"), 1)

    def test_invalid_stroke_skipped(self):
        board = {"strokes": ["not a dict",
                             {"color": "#000", "width": 4, "points": [[0, 0]]}]}
        s = analyze_board(board)
        self.assertEqual(s["total"], 1)
        self.assertEqual(s["normal"], 1)


class TestEnrichedLocal(unittest.TestCase):
    """验证本地答疑升级为「板面感知」：依据真实统计给出有针对性的提示。"""

    def test_board_aware_local_answer(self):
        ai = AITeacher()
        board = {"strokes": [
            {"color": "#1A1A1A", "width": 4, "points": [[0, 0]]},
            {"color": "#E53935", "width": 6, "points": [[1, 1]]},
        ]}
        ans = ai.ask("什么是函数？", board_dict_to_text(board), board=board)
        self.assertIn("什么是函数？", ans)
        self.assertIn("2", ans)               # 总条数
        self.assertIn("#1a1a1a", ans)         # 配色被纳入答复
        self.assertIn("#e53935", ans)

    def test_empty_board_local_hint(self):
        ai = AITeacher()
        ans = ai.ask("讲什么？", "(空白白板)", board={"strokes": []})
        self.assertIn("为空", ans)
        self.assertIn("讲什么？", ans)

    def test_eraser_and_colors_in_answer(self):
        ai = AITeacher()
        board = {"strokes": [
            {"color": "#1A1A1A", "width": 4, "points": [[0, 0]]},
            {"color": "#E53935", "width": 6, "points": [[1, 1]]},
            {"color": "#1A1A1A", "width": 2, "eraser": True, "points": [[2, 2]]},
        ]}
        ans = ai.ask("这题怎么讲", board_dict_to_text(board), board=board)
        self.assertIn("橡皮修正", ans)         # 含 1 处橡皮修正
        self.assertIn("2 种颜色", ans)          # 使用 2 种颜色分层标注


class TestRemoteStats(unittest.TestCase):
    """验证远程请求在传入 board 时携带结构化 stats 上下文。"""

    def test_remote_payload_includes_stats(self):
        from unittest import mock

        captured = {}

        def fake_post(url, payload, api_key=None, timeout=8.0):
            captured["payload"] = payload
            return '{"answer": "ok"}'

        ai = AITeacher(endpoint="https://api.example/ai", api_key="k")
        board = {"strokes": [{"color": "#000", "width": 4, "points": [[0, 0]]}]}
        with mock.patch("smartboard.ai_link._http_post_json", side_effect=fake_post):
            ai.ask("q?", board_dict_to_text(board), board=board)
        self.assertIn("stats", captured["payload"])
        self.assertEqual(captured["payload"]["stats"]["total"], 1)
        self.assertEqual(captured["payload"]["stats"]["normal"], 1)


if __name__ == "__main__":
    prog = unittest.main(verbosity=2, exit=False)
    _cleanup_qt()
    os._exit(0 if prog.result.wasSuccessful() else 1)

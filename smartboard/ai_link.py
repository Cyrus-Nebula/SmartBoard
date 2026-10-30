"""智学白板 SmartBoard —— AI 教学联动（可插拔：默认本地答疑，可配置远程后端）。

兼容目标：Python 3.7+ / PySide6 6.2.4+。

设计要点：
- 本模块不依赖任何 GUI / Qt 组件，仅基于白板的 JSON 序列化结构（dict）工作，
  因此既能被 MainWindow 在运行时调用，也能被纯单元测试覆盖，无需显示环境。
- 默认走本地规则答疑（mock），无需联网即可演示与测试；
- 若配置了 endpoint（环境变量 SMARTBOARD_AI_ENDPOINT / SMARTBOARD_AI_KEY 注入，
  不写死密钥），则预留远程调用钩子，便于将来接真实大模型而不改动界面代码。
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional


def board_dict_to_text(board):
    """把 WhiteboardScene.to_dict() 产出的结构转为便于 AI 理解的文本快照。

    board 形如 {"version": 1, "strokes": [ {color,width,eraser,points}, ... ]}。
    """
    if not isinstance(board, dict):
        return "(空白白板)"
    strokes = board.get("strokes", []) or []
    lines = []
    for idx, s in enumerate(strokes, start=1):
        if not isinstance(s, dict):
            continue
        kind = "橡皮擦" if s.get("eraser") else "笔迹"
        pts = s.get("points", []) or []
        # 仅采样首尾若干点，避免超长快照淹没关键信息
        if len(pts) > 5:
            sample = pts[:3] + ["..."] + pts[-2:]
        else:
            sample = pts
        pts_text = ", ".join(_fmt_point(p) for p in sample)
        lines.append("第{}条{} 颜色{} 线宽{} 采样点: {}".format(
            idx, kind, s.get("color", "?"), s.get("width", "?"), pts_text))
    if not lines:
        return "(空白白板)"
    return "\n".join(lines)


def _fmt_point(p):
    if isinstance(p, (list, tuple)) and len(p) >= 2:
        return "({:.0f},{:.0f})".format(p[0], p[1])
    return str(p)


class AITeacher:
    """AI 教学联动核心。"""

    def __init__(self, endpoint=None, api_key=None):
        self.endpoint = endpoint or os.environ.get("SMARTBOARD_AI_ENDPOINT")
        self.api_key = api_key or os.environ.get("SMARTBOARD_AI_KEY")

    def ask(self, question, context=""):
        if not question or not question.strip():
            return "请先提出一个教学问题。"
        if self.endpoint:
            return self._ask_remote(question, context)
        return self._ask_local(question, context)

    def _ask_local(self, question, context):
        # 粗略统计白板条数（每条快照以「第N条」开头）
        n = context.count("第") if context else 0
        return ("[本地教学助手] 收到问题：{q}\n"
                "当前白板共 {n} 条内容，建议结合板面图形逐步讲解，"
                "必要时用不同颜色高亮关键点。").format(q=question, n=n)

    def _ask_remote(self, question, context):
        # 预留真实后端调用：实际接入时可用 requests 将 (question, context)
        # POST 到 self.endpoint。本体不在此发起网络请求，保持离线可用与可测试。
        return "[远程模式已配置但未启用网络请求] " + self._ask_local(question, context)

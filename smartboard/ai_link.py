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

import json
import os
import urllib.error
import urllib.request
from typing import Dict, List, Optional


def board_dict_to_text(board):
    """把 WhiteboardScene.to_dict() 产出的结构转为便于 AI 理解的文本快照。

    board 形如 {"version": 1, "strokes": [ {color,width,eraser,points}, ... ]}。
    """
    if not isinstance(board, dict):
        return "(空白白板)"
    strokes = board.get("strokes", []) or []
    # 概览行：先给 AI 一个总体统计，便于快速把握板面规模与配色，
    # 再附逐条采样点细节；两者互补，比纯罗列采样点更有信息量。
    stats = analyze_board(board)
    if stats["total"] == 0:
        return "(空白白板)"
    lines = [
        "白板概览：共 {total} 条内容（笔迹 {normal} 条"
        "{erase}），使用 {ncolor} 种颜色。".format(
            total=stats["total"],
            normal=stats["normal"],
            erase=("，橡皮修正 %d 次" % stats["eraser"]) if stats["eraser"] else "",
            ncolor=len(stats["colors"]),
        )
    ]
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
    return "\n".join(lines)


def _fmt_point(p):
    if isinstance(p, (list, tuple)) and len(p) >= 2:
        return "({:.0f},{:.0f})".format(p[0], p[1])
    return str(p)


def _stroke_points(s):
    """把一条笔迹的 points 字段解析为 [(x, y), ...] 浮点坐标列表（容错、去脏数据）。

    仅用标准库，保持本模块「无 GUI 依赖、兼容 Python 3.7」的特性，便于纯单测覆盖。
    """
    out = []
    for p in (s.get("points", []) or []):
        if isinstance(p, (list, tuple)) and len(p) >= 2:
            try:
                out.append((float(p[0]), float(p[1])))
            except (TypeError, ValueError):
                continue
    return out


def _normalize_color(c):
    """把任意形式的颜色描述归一为 lowercase 6 位 hex（如 '#FFF' -> '#ffffff'）。

    无法识别的输入返回 None。仅用字符串操作，不依赖 QColor，保持本模块「无 GUI
    依赖、兼容 Python 3.7 标准库」的特性，便于纯单元测试覆盖。
    """
    if not isinstance(c, str):
        return None
    h = c.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    if len(h) == 6 and all(ch in "0123456789abcdefABCDEF" for ch in h):
        return "#" + h.lower()
    return None


def analyze_board(board):
    """把白板 dict 解析为可量化的教学统计（确定性、纯标准库、无 GUI 依赖）。

    返回 dict：
      - total / normal / eraser：总条数 / 普通笔迹数 / 橡皮擦条数；
      - highlighted：荧光笔（高亮）笔迹数（仅统计非橡皮笔迹）；
      - colors：{归一 hex: 出现次数}（仅统计普通笔迹，便于识别教师配色习惯）；
      - dominant_color：出现最多的颜色（无笔迹时为 None）；
      - ink_length：所有非橡皮笔迹的采样点折线总长（场景坐标 px），量化「板书量」；
      - bbox：所有非橡皮笔迹的包围盒 {"x","y","w","h"}（无笔迹时为 None），反映版面布局；
      - empty：是否空白白板。
    这些量化指标让本地答疑能给出「板书偏少 / 建议用荧光笔突出重点」等可执行的板面建议，
    也可作为结构化上下文随远程请求一并发送，让真实大模型更快把握板面规模与配色，
    无需逐行解析采样点文本。
    """
    total = normal = eraser = highlighted = 0
    colors = {}
    ink_length = 0.0
    min_x = min_y = None
    max_x = max_y = None
    if isinstance(board, dict):
        for s in (board.get("strokes", []) or []):
            if not isinstance(s, dict):
                continue
            total += 1
            is_eraser = bool(s.get("eraser"))
            if is_eraser:
                eraser += 1
            else:
                normal += 1
                if bool(s.get("highlighter")):
                    highlighted += 1
                c = _normalize_color(s.get("color"))
                if c:
                    colors[c] = colors.get(c, 0) + 1
            # 仅统计「非橡皮」笔迹的板书量与版面（橡皮是修正动作，不应计入有效板书面积）
            if not is_eraser:
                pts = _stroke_points(s)
                for i in range(1, len(pts)):
                    dx = pts[i][0] - pts[i - 1][0]
                    dy = pts[i][1] - pts[i - 1][1]
                    ink_length += (dx * dx + dy * dy) ** 0.5
                for x, y in pts:
                    if min_x is None or x < min_x:
                        min_x = x
                    if min_y is None or y < min_y:
                        min_y = y
                    if max_x is None or x > max_x:
                        max_x = x
                    if max_y is None or y > max_y:
                        max_y = y
    dominant = max(colors.items(), key=lambda kv: kv[1])[0] if colors else None
    bbox = (
        None if min_x is None
        else {"x": min_x, "y": min_y, "w": max_x - min_x, "h": max_y - min_y}
    )
    return {
        "total": total,
        "normal": normal,
        "eraser": eraser,
        "highlighted": highlighted,
        "colors": colors,
        "dominant_color": dominant,
        "ink_length": ink_length,
        "bbox": bbox,
        "empty": total == 0,
    }


class AITeacher:
    """AI 教学联动核心。

    默认走本地规则答疑（无需联网即可演示与测试）；若配置了 endpoint
    （环境变量 SMARTBOARD_AI_ENDPOINT / SMARTBOARD_AI_KEY 注入，不写死密钥），
    则发起一次标准库 HTTP 请求，兼容纯文本 / JSON / OpenAI 风格应答。
    任何网络异常都**优雅降级**到本地答疑，绝不抛出中断白板使用。
    """

    def __init__(self, endpoint=None, api_key=None, timeout=8.0):
        self.endpoint = endpoint or os.environ.get("SMARTBOARD_AI_ENDPOINT")
        self.api_key = api_key or os.environ.get("SMARTBOARD_AI_KEY")
        # 单次远程请求超时（秒）：过长会卡住界面，过短易误判为失败。
        # 仅作用于已配置 endpoint 的远程模式，默认本地模式不受影响。
        self.timeout = float(timeout)

    def ask(self, question, context="", board=None):
        """答疑入口。

        - question：用户问题（必填）；
        - context：便于 AI 理解的白板文本快照（可选，仍可单独使用）；
        - board：WhiteboardScene.to_dict() 的结构化白板（可选，强烈建议传入）：
          传入后会做精确的结构化统计，使本地答疑「板面感知」、远程请求带上
          stats 上下文；不传则保持旧行为（从 context 文本粗略统计），向后兼容。
        """
        if not question or not question.strip():
            return "请先提出一个教学问题。"
        stats = analyze_board(board) if board is not None else None
        if self.endpoint:
            return self._ask_remote(question, context, stats=stats)
        return self._ask_local(question, context, stats=stats)

    def _ask_local(self, question, context="", stats=None):
        # 未传入结构化白板时，退化为旧逻辑：从 context 文本粗略统计条数
        if stats is None:
            total = context.count("第") if context else 0
            stats = {
                "total": total, "normal": total, "eraser": 0, "highlighted": 0,
                "colors": {}, "dominant_color": None,
                "ink_length": 0.0, "bbox": None, "empty": total == 0,
            }
        if stats.get("empty"):
            return ("[本地教学助手] 收到问题：{q}\n"
                    "当前白板为空，建议先书写板书或图形，再向我提问，"
                    "便于结合板面内容给出针对性讲解。").format(q=question)

        total = stats["total"]
        normal = stats["normal"]
        eraser = stats["eraser"]
        colors = stats.get("colors", {}) or {}

        # 依据真实板面特征生成有帮助的提示（而非泛泛而谈）
        tips = []
        if eraser:
            tips.append("含 %d 处橡皮修正" % eraser)
        if len(colors) >= 2:
            tips.append("使用 %d 种颜色分层标注" % len(colors))
        extra = ("（" + "，".join(tips) + "）") if tips else ""
        color_desc = (
            "、".join("%s ×%d" % (c, n) for c, n in colors.items())
            or "默认黑"
        )
        answer = ("[本地教学助手] 收到问题：{q}\n"
                  "当前白板共 {total} 条内容（笔迹 {normal} 条{extra}），"
                  "主要配色：{color_desc}。").format(
                      q=question, total=total, normal=normal,
                      extra=extra, color_desc=color_desc)

        # 板面感知的「可执行」教学建议：基于量化指标给出下一步动作，
        # 而非泛泛的「按步骤讲解」。仅在确有可操作点时附加，避免空话。
        suggestions = self._board_suggestions(stats)
        if suggestions:
            answer += "\n\n教学建议：\n" + "\n".join("· " + s for s in suggestions)
        return answer

    def _board_suggestions(self, stats):
        """基于 analyze_board 的量化指标，给出针对性的板面教学建议（确定性、纯逻辑）。

        设计原则：建议必须是「可执行的下一步动作」，而非重复统计。重点识别两类课堂痛点：
        (1) 板书量偏少 —— 学生跟不上节奏，建议补充步骤/图示；
        (2) 已有一定内容却未用荧光笔 —— 关键结论易被淹没，建议高亮突出重点。
        另对「已用高亮 / 含橡皮修正（即时纠错）/ 多色分层」给出顺势引导。
        """
        suggestions = []
        normal = stats.get("normal", 0)
        highlighted = stats.get("highlighted", 0)
        eraser = stats.get("eraser", 0)
        ink = stats.get("ink_length", 0.0)
        colors = stats.get("colors", {}) or {}

        # 板书量偏少：单/双笔且总书写路径过短（经验阈值 600 场景 px，约一两条短线的量），
        # 提示教师补充关键步骤与图示，避免学生跟不上节奏。
        if normal <= 2 and ink < 600.0:
            suggestions.append(
                "板面内容偏少，建议补充关键步骤与图示，便于学生跟上节奏")
        # 已有一定内容但尚未使用高亮：关键结论易被淹没，建议用荧光笔突出重点。
        if highlighted == 0 and normal >= 3:
            suggestions.append(
                "本板尚未使用荧光笔，可用它高亮关键结论/公式，让重点一目了然")
        # 已用高亮：顺势引导教师聚焦高亮区域讲解。
        if highlighted > 0:
            suggestions.append(
                "已用荧光笔突出重点，讲解时可优先聚焦高亮区域")
        # 课堂即时纠错：把橡皮修正转化为「此处易错点」的讲解机会。
        if eraser > 0:
            suggestions.append(
                "含 %d 处橡皮修正（课堂即时纠错），可顺势讲解此处易错点" % eraser)
        # 多色分层：肯定其结构清晰，鼓励继续用颜色区分/对比。
        if len(colors) >= 3:
            suggestions.append(
                "已用 %d 种颜色分层标注，对比与强调更清晰" % len(colors))
        return suggestions

    def _ask_remote(self, question, context="", stats=None):
        """已配置远程后端时，真正发起一次 HTTP 请求并把答复回传。

        设计约束：
        - 仅用标准库 urllib，无第三方依赖，兼容 Python 3.7 / 离线环境；
        - 带超时（默认 8s），不把界面长时间卡死；
        - 任何失败（网络不可达 / 超时 / 非 200 / 响应畸形）一律**优雅降级**
          到本地规则答疑，并附一句提示，绝不抛异常中断白板使用。
        - 传入 board 统计时，把结构化 stats 一并放进请求体，让远程大模型
          更快把握板面规模与配色（context 文本仍保留以兼容既有后端）。
        """
        try:
            payload = {"question": question, "context": context}
            if stats is not None:
                payload["stats"] = stats
            raw = _http_post_json(
                self.endpoint,
                payload,
                api_key=self.api_key,
                timeout=self.timeout,
            )
            answer = _extract_answer(raw)
            if not answer:
                return ("[远程后端未返回有效答复，已回退本地答疑]\n"
                        + self._ask_local(question, context, stats=stats))
            return "[远程 AI 答疑]\n" + answer
        except Exception as exc:  # noqa: BLE001 —— 主动兜底，降级而非崩溃
            return ("[远程后端不可用，已回退本地答疑：%s]\n" % _safe_msg(exc)
                    + self._ask_local(question, context, stats=stats))


def _http_post_json(url, payload, api_key=None, timeout=8.0):
    """用标准库 urllib 发送 JSON POST，返回解码后的响应体（str）。

    仅依赖 Python 3.7 标准库，不引入 requests 等第三方依赖，保证兼容性。
    任何网络 / 超时 / HTTP 错误都会抛出，由调用方决定如何降级。
    """
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain",
        },
    )
    if api_key:
        req.add_header("Authorization", "Bearer %s" % api_key)
    # urlopen 在 4xx/5xx 时抛 HTTPError（URLError 子类），统一交由调用方降级
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def _extract_answer(raw):
    """从响应体稳健提取答复文本。

    兼容三种常见格式：
    - 纯文本
    - JSON {"answer": "..."}
    - OpenAI 风格 {"choices":[{"message":{"content":"..."}}]}
    解析失败或字段缺失时退化为原始文本（兜底），空响应返回 ""。
    """
    text = (raw or "").strip()
    if not text:
        return ""
    try:
        obj = json.loads(text)
    except (ValueError, TypeError):
        return text
    if isinstance(obj, dict):
        if "answer" in obj and obj["answer"]:
            return str(obj["answer"])
        choices = obj.get("choices")
        if isinstance(choices, list) and choices:
            msg = (choices[0] or {}).get("message") or {}
            content = msg.get("content")
            if content:
                return str(content)
        # 兜底：取第一个看起来像正文的字符串值
        for value in obj.values():
            if isinstance(value, str) and value.strip():
                return value
    return text


def _safe_msg(exc):
    """把异常转成一句话文案（网络错误常无友好 str，回退到类名）。"""
    return str(exc) or exc.__class__.__name__

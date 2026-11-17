"""智学白板 SmartBoard —— 草稿自动保存与崩溃恢复（纯逻辑模块）。

兼容目标：Python 3.7+（仅标准库，无 PySide6 / GUI 依赖）。

背景 / 价值
------------
白板已有「清空可撤销」「载入原子性」两层数据保护，但进程崩溃、断电、误关窗口时，
尚未手动保存的板书仍会全部丢失。本模块把「崩溃恢复」这最后一环补齐：定时把场景
快照原子写入一个独立草稿文件，下次启动时若检测到草稿则提示教师一键恢复。

设计约束（对既有功能零侵入）
----------------------------
- 草稿是独立文件，不修改用户手动保存的白板文件；读写只复用 scene.to_dict() /
  load_dict()，不触碰笔迹模型、渲染、导出、序列化格式；
- 纯标准库 + 确定性逻辑，可 headless 单元测试，无需 QApplication；
- 原子落盘（临时文件 + os.replace），断电/中断也不会留下半截损坏的目标文件；
- 读取对一切损坏（非法 JSON / 版本不符 / 字段缺失）宽容：返回 None 且不抛异常，
  绝不因坏草稿阻断白板启动；
- 仅在本模块内部使用 datetime，时间戳为 ISO-8601 字符串，Python 3.7 可解析。
"""

from __future__ import annotations

import datetime
import json
import os
import tempfile
from typing import Dict, Optional

# 草稿格式版本：将来结构升级时递增，旧版本草稿会被安全忽略（不强行解析）。
DRAFT_VERSION = 1

# 默认自动保存间隔（毫秒）。主窗口 QTimer 使用；本模块不依赖 Qt。
DEFAULT_INTERVAL_MS = 30 * 1000

# 草稿文件名（位于用户目录或显式指定的目录下）。
DRAFT_FILENAME = "smartboard_autosave.json"

# 草稿内标识应用与格式版本，避免误读其它 JSON 文件。
_APP_TAG = "SmartBoard"
_KEY_APP = "app"
_KEY_VERSION = "version"
_KEY_SAVED_AT = "saved_at"
_KEY_BOARD = "board"


def default_draft_path():
    """返回默认草稿文件路径。

    覆盖优先级：
    1) 环境变量 SMARTBOARD_DRAFT_DIR —— 若指定，视为「草稿目录」，在其下放
       DRAFT_FILENAME；测试可用它把草稿隔离到临时目录，部署也可自定义位置；
    2) 用户主目录下的固定文件名；
    3) home 不可用时回退系统临时目录。
    仅用 os / tempfile，纯标准库。
    """
    env_dir = os.environ.get("SMARTBOARD_DRAFT_DIR")
    if env_dir:
        return os.path.join(env_dir, DRAFT_FILENAME)
    try:
        base = os.path.expanduser("~")
        # home 不可写时（极端受限环境）回退临时目录，保证自动保存仍能工作
        if not base or not os.path.isdir(base):
            base = tempfile.gettempdir()
    except Exception:  # noqa: BLE001 —— 环境探测兜底
        base = tempfile.gettempdir()
    return os.path.join(base, DRAFT_FILENAME)


def _now_iso():
    """当前本地时间的 ISO-8601 字符串（秒精度），供草稿时间戳与展示使用。"""
    return datetime.datetime.now().isoformat(timespec="seconds")


class DraftManager:
    """草稿文件的原子读写与清理。

    典型用法（主窗口）：
        mgr = DraftManager()                       # 默认路径
        mgr.save(scene.to_dict())                  # 定时/退出时原子落盘
        board = mgr.load()                         # 启动时读取；损坏返回 None
        mgr.clear()                                # 手动保存/载入/恢复后清除草稿

    也可用 tempfile 目录构造以做隔离测试：DraftManager(path=...).
    """

    def __init__(self, path=None):
        # type: (Optional[str]) -> None
        self.path = path if path else default_draft_path()

    # -- 写 --
    def save(self, board):
        # type: (Dict) -> bool
        """把白板 dict 原子写入草稿文件，成功返回 True。

        原子性：先写同目录临时文件并 flush/fsync，再 os.replace 到目标路径，
        因此任意时刻目标文件要么是旧完整草稿、要么是新完整草稿，不会是半截文件。
        非法入参（非 dict）或 IO 异常一律返回 False，不抛给调用方（自动保存不可
        影响白板主流程）。
        """
        if not isinstance(board, dict):
            return False
        payload = {
            _KEY_APP: _APP_TAG,
            _KEY_VERSION: DRAFT_VERSION,
            _KEY_SAVED_AT: _now_iso(),
            _KEY_BOARD: board,
        }
        directory = os.path.dirname(self.path) or "."
        tmp_path = self.path + ".tmp"
        try:
            if not os.path.isdir(directory):
                os.makedirs(directory)
            with open(tmp_path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False)
                fh.flush()
                try:
                    os.fsync(fh.fileno())
                except (OSError, AttributeError):
                    # 某些平台/文件系统不支持 fsync，忽略即可，os.replace 仍原子
                    pass
            os.replace(tmp_path, self.path)
            return True
        except (OSError, TypeError, ValueError):
            # 写失败时尽量清理临时文件，避免残留；不影响目标文件既有内容
            self._remove_quietly(tmp_path)
            return False

    # -- 读 --
    def load(self):
        # type: () -> Optional[Dict]
        """读取草稿中的白板 dict；无草稿 / 损坏 / 版本不符均返回 None（不抛异常）。"""
        payload = self._read_payload()
        return self._extract_board(payload)

    def info(self):
        # type: () -> Optional[Dict]
        """返回草稿元信息（saved_at / version / stroke_count）；无效草稿返回 None。

        供启动时「检测到草稿，是否恢复？」提示展示时间等信息，不加载完整笔迹。
        """
        payload = self._read_payload()
        if not self._is_valid(payload):
            return None
        board = payload[_KEY_BOARD]
        strokes = board.get("strokes", []) if isinstance(board, dict) else []
        return {
            "saved_at": payload.get(_KEY_SAVED_AT),
            "version": payload.get(_KEY_VERSION),
            "stroke_count": len(strokes) if isinstance(strokes, list) else 0,
        }

    def has_draft(self):
        # type: () -> bool
        """是否存在「有效且非空」的草稿（空板草稿视为无恢复价值）。"""
        info = self.info()
        return bool(info and info.get("stroke_count", 0) > 0)

    def clear(self):
        # type: () -> bool
        """删除草稿（含可能残留的临时文件），返回是否删除了任意文件。"""
        removed = False
        for p in (self.path, self.path + ".tmp"):
            if self._remove_quietly(p):
                removed = True
        return removed

    # -- 内部 --
    def _read_payload(self):
        # type: () -> Optional[Dict]
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, ValueError, TypeError):
            return None
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _is_valid(payload):
        # type: (Optional[Dict]) -> bool
        return (
            isinstance(payload, dict)
            and payload.get(_KEY_APP) == _APP_TAG
            and payload.get(_KEY_VERSION) == DRAFT_VERSION
            and isinstance(payload.get(_KEY_BOARD), dict)
        )

    @classmethod
    def _extract_board(cls, payload):
        # type: (Optional[Dict]) -> Optional[Dict]
        if not cls._is_valid(payload):
            return None
        return payload[_KEY_BOARD]

    @staticmethod
    def _remove_quietly(path):
        # type: (str) -> bool
        try:
            if os.path.exists(path):
                os.remove(path)
                return True
        except OSError:
            pass
        return False

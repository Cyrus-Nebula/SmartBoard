"""智学白板 SmartBoard —— 主窗口与工具栏。

兼容目标：Python 3.7 / PySide6 6.2.4。
"""

import json
import os

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QColor, QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QMainWindow, QToolBar, QPushButton, QSlider, QFileDialog, QMessageBox, QWidget,
)

from .whiteboard import WhiteboardScene
from .view import WhiteboardView

_PALETTE = ["#1A1A1A", "#E53935", "#1E88E5", "#43A047", "#FB8C00", "#8E24AA"]


class MainWindow(QMainWindow):
    def __init__(self, parent=None):
        super(MainWindow, self).__init__(parent)
        self.setWindowTitle("智学白板 SmartBoard")
        self.resize(1000, 700)

        self.board = WhiteboardView(WhiteboardScene(), self)
        self.setCentralWidget(self.board)

        # 当前激活画笔颜色（用于工具栏高亮），默认取调色板第一色
        self._active_color = _PALETTE[0]
        self._color_buttons = {}   # hex -> QPushButton

        self._build_toolbar()
        self._install_shortcuts()

        # 工具栏状态联动：场景笔迹变化时刷新撤销/重做/清空按钮可用态
        self.board.scene().strokesChanged.connect(self._refresh_actions)
        self._refresh_color_buttons()
        self._refresh_actions()

    # -- 工具栏 --
    def _build_toolbar(self):
        tb = QToolBar("工具", self)
        tb.setIconSize(QSize(28, 28))
        tb.setMovable(False)
        self.addToolBar(tb)

        # 颜色按钮
        for hex_color in _PALETTE:
            btn = QPushButton("")
            btn.setFixedSize(26, 26)
            btn.setStyleSheet(
                "background-color: %s; border: 1px solid #888;" % hex_color
            )
            btn.setToolTip("画笔颜色 %s" % hex_color)
            btn.clicked.connect(
                lambda _checked=False, c=hex_color: self._pick_color(c)
            )
            tb.addWidget(btn)
            self._color_buttons[hex_color] = btn

        tb.addSeparator()

        # 线宽
        tb.addWidget(self._label("线宽"))
        self._width = QSlider(Qt.Horizontal)
        self._width.setRange(1, 40)
        self._width.setValue(self.board.pen_width)
        self._width.setFixedWidth(120)
        self._width.valueChanged.connect(self._on_width)
        tb.addWidget(self._width)

        tb.addSeparator()

        # 橡皮擦
        self._eraser_act = self._add_action(tb, "橡皮擦", self._toggle_eraser)
        self._eraser_act.setCheckable(True)

        tb.addSeparator()

        # 撤销 / 重做 / 清空
        self._undo_act = self._add_action(tb, "撤销", self.board.scene().undo)
        self._redo_act = self._add_action(tb, "重做", self.board.scene().redo)
        self._clear_act = self._add_action(tb, "清空", self._clear)

        tb.addSeparator()

        # 保存 / 载入
        self._add_action(tb, "保存", self._save)
        self._add_action(tb, "载入", self._load)

    def _label(self, text):
        from PySide6.QtWidgets import QLabel
        w = QLabel(text)
        w.setStyleSheet("padding: 0 4px;")
        return w

    def _add_action(self, tb, text, slot):
        act = QAction(text, self)
        act.triggered.connect(slot)
        tb.addAction(act)
        return act

    # -- 键盘快捷键 --
    def _install_shortcuts(self):
        """为常用操作绑定键盘快捷键，提升白板书写与编辑效率。

        仅使用 Qt 6.2.4 已具备的 QShortcut / QKeySequence 公共 API，
        兼容 Python 3.7。快捷键全部挂在主窗口（WindowShortcut 上下文），
        不影响既有工具栏按钮与鼠标书写流程。
        """
        def add(key, slot):
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.WindowShortcut)
            sc.activated.connect(slot)
            return sc

        scene = self.board.scene()
        add("Ctrl+Z", scene.undo)                       # 撤销
        add("Ctrl+Shift+Z", scene.redo)                 # 重做（Mac/通用）
        add("Ctrl+Y", scene.redo)                       # 重做（Windows 习惯）
        add("Ctrl+L", self._clear)                      # 清空
        add("E", lambda: self._toggle_eraser(not self.board.eraser))  # 橡皮擦开关
        add("Ctrl+S", self._save)                       # 保存
        add("Ctrl+O", self._load)                       # 载入

    # -- 状态联动 --
    def _refresh_color_buttons(self):
        """高亮当前激活画笔颜色；橡皮擦激活时所有颜色按钮降亮。"""
        eraser_on = self.board.eraser
        for hex_color, btn in self._color_buttons.items():
            active = (not eraser_on) and (hex_color == self._active_color)
            if active:
                btn.setStyleSheet(
                    "background-color: %s; border: 3px solid #FFD54F;" % hex_color
                )
            else:
                btn.setStyleSheet(
                    "background-color: %s; border: 1px solid #888;" % hex_color
                )

    def _refresh_actions(self):
        """根据场景状态启用/禁用 撤销 / 重做 / 清空 按钮。"""
        scene = self.board.scene()
        self._undo_act.setEnabled(scene.can_undo())
        self._redo_act.setEnabled(scene.can_redo())
        self._clear_act.setEnabled(scene.count() > 0)

    # -- 回调 --
    def _pick_color(self, hex_color):
        self.board.set_pen_color(hex_color)
        self._active_color = hex_color
        self._eraser_act.setChecked(False)
        self._refresh_color_buttons()

    def _on_width(self, value):
        self.board.set_pen_width(value)

    def _toggle_eraser(self, checked):
        self.board.set_eraser(checked)
        # 橡皮擦激活时取消颜色高亮，关闭时恢复
        self._refresh_color_buttons()

    def _clear(self):
        self.board.scene().clear_all()

    def _save(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "保存白板", os.path.expanduser("~"), "白板文件 (*.json)"
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.board.scene().to_json())
        except OSError as e:
            QMessageBox.warning(self, "保存失败", str(e))

    def _load(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "载入白板", os.path.expanduser("~"), "白板文件 (*.json)"
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.board.scene().load_dict(data)
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, "载入失败", str(e))

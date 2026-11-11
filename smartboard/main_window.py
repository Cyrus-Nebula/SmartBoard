"""智学白板 SmartBoard —— 主窗口与工具栏。

兼容目标：Python 3.7 / PySide6 6.2.4。
"""

import json
import os

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QColor, QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QMainWindow, QToolBar, QPushButton, QSlider, QFileDialog, QMessageBox, QWidget, QLabel,
)

from .whiteboard import WhiteboardScene
from .view import WhiteboardView
from .ai_link import AITeacher, board_dict_to_text

_PALETTE = ["#1A1A1A", "#E53935", "#1E88E5", "#43A047", "#FB8C00", "#8E24AA"]


class MainWindow(QMainWindow):
    def __init__(self, parent=None):
        super(MainWindow, self).__init__(parent)
        self.setWindowTitle("智学白板 SmartBoard")
        self.resize(1000, 700)

        self.board = WhiteboardView(WhiteboardScene(), self)
        self.board.setToolTip(
            "左键书写 · 右键删除笔迹 · 中键拖拽平移 · 滚轮缩放 · "
            "E 切换橡皮擦 · Ctrl+Z 撤销"
        )
        self.setCentralWidget(self.board)

        # AI 教学联动：默认本地答疑，可经环境变量接入远程后端
        self._ai = AITeacher()

        # 当前激活画笔颜色（用于工具栏高亮），默认取调色板第一色
        self._active_color = _PALETTE[0]
        self._color_buttons = {}   # hex -> QPushButton

        self._build_toolbar()
        self._build_statusbar()
        self._install_shortcuts()

        # 工具栏状态联动：场景笔迹变化时刷新撤销/重做/清空按钮可用态
        self.board.scene().strokesChanged.connect(self._refresh_actions)
        self._refresh_color_buttons()
        self._refresh_actions()

        # 视图变换（缩放 / 适应内容）时同步刷新状态栏缩放百分比；
        # transformChanged 为 Qt 5.14+ 公共信号，PySide6 6.2.4 已具备。
        # 用 try 包一层以兼容更老环境，缺失该信号也不影响其余功能。
        try:
            self.board.transformChanged.connect(self._update_status)
        except Exception:
            pass

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
        self._eraser_act.setToolTip("橡皮擦：以背景色覆盖擦除；右键点击任意笔迹可整条删除")

        # 荧光笔（高亮笔）：半透明叠加，强调关键结论/公式而不遮挡其下文字
        self._hl_act = self._add_action(tb, "荧光笔", self._toggle_highlighter)
        self._hl_act.setCheckable(True)
        self._hl_act.setToolTip(
            "荧光笔：半透明高亮笔，叠在板书上突出关键结论/公式，不遮挡其下文字"
        )

        tb.addSeparator()

        # 撤销 / 重做 / 清空
        self._undo_act = self._add_action(tb, "撤销", self.board.scene().undo)
        self._redo_act = self._add_action(tb, "重做", self.board.scene().redo)
        self._clear_act = self._add_action(tb, "清空", self._clear)

        tb.addSeparator()

        # 保存 / 载入 / 导出图片
        self._add_action(tb, "保存", self._save)
        self._add_action(tb, "载入", self._load)
        self._add_action(tb, "导出图片", self._export)
        self._add_action(tb, "重置视图", self.board.reset_view)
        self._add_action(tb, "适应内容", self.board.fit_to_content)

        tb.addSeparator()

        # 书写辅助网格：可勾选的淡色参考线，帮助对齐与保持书写水平
        self._grid_act = self._add_action(tb, "网格", self._toggle_grid)
        self._grid_act.setCheckable(True)
        self._grid_act.setToolTip("书写辅助网格：淡淡的参考线，帮助对齐与保持书写水平")

        tb.addSeparator()

        # 自然笔（速度感知变宽）：开启后笔迹随书写速度变化粗细，模拟真实钢笔/毛笔手感
        self._natural_act = self._add_action(tb, "自然笔", self._toggle_natural)
        self._natural_act.setCheckable(True)
        self._natural_act.setToolTip(
            "自然笔：开启后笔迹随书写速度变化粗细（慢笔粗、快笔细），模拟真实钢笔/毛笔手感"
        )

        tb.addSeparator()

        # AI 教学联动：将当前白板内容作为上下文，向 AI 提问
        self._add_action(tb, "AI 答疑", self._ask_ai)

    def _build_statusbar(self):
        """在底部状态栏常驻一块「实时状态」标签，反映当前工具与白板概况。

        目的：课堂场景下教师常远离屏幕或经低分辨率投影授课，工具栏按钮的
        高亮（如颜色边框）不易察觉；状态栏以文字形式常驻展示
        「工具 / 颜色 / 线宽 / 笔迹数 / 可撤销/重做」，使书写状态一目了然。
        该标签仅做展示，不参与任何笔迹逻辑，对既有功能零侵入。
        """
        label = QLabel()
        label.setStyleSheet("padding: 0 8px;")
        # 右侧常驻部件，窗口缩放时保持可见
        self.statusBar().addPermanentWidget(label)
        self._status_label = label
        self._update_status()

    def _update_status(self):
        """根据当前画笔/橡皮擦/荧光笔状态与场景笔迹数，刷新状态栏文本。

        纯展示逻辑：读取 self.board / self._active_color / 场景计数，不做任何写操作，
        因此可安全地挂在 strokesChanged 信号链路上随笔迹变化自动刷新。
        """
        scene = self.board.scene()
        if self.board.eraser:
            tool = "橡皮擦"
            color_part = ""                      # 橡皮擦模式下颜色无意义，省略
        elif self.board.highlighter:
            tool = "荧光笔"
            color_part = " | 颜色 " + self._active_color
        else:
            tool = "画笔"
            color_part = " | 颜色 " + self._active_color
        width_part = " | 线宽 %dpx" % self.board.pen_width
        grid_part = " | 网格 开" if self.board.is_grid_enabled() else " | 网格 关"
        natural_part = " | 自然笔 开" if self.board.variable_width else " | 自然笔 关"
        # 实时缩放百分比：屏幕像素 ↔ 场景坐标的缩放系数 ×100（默认 100%）。
        # 只读视图变换矩阵，不改变任何笔迹/状态，完全无副作用。
        scale = self.board.transform().m11()
        if scale <= 0.0:
            scale = 1.0
        zoom_part = " | 缩放 %d%%" % int(round(scale * 100.0))
        undo = "可撤销" if scene.can_undo() else "不可撤销"
        redo = "可重做" if scene.can_redo() else "不可重做"
        text = "工具：%s%s%s%s%s%s | 笔迹 %d 条 | %s | %s" % (
            tool, color_part, width_part, grid_part, natural_part, zoom_part,
            scene.count(), undo, redo,
        )
        self._status_label.setText(text)

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
        # 画布缩放与复位（交互增强）：滚轮也可缩放，中键拖拽平移
        add("Ctrl++", lambda: self.board.zoomBy(1.1))           # 放大
        add("Ctrl+-", lambda: self.board.zoomBy(1.0 / 1.1))      # 缩小
        add("Ctrl+0", self.board.reset_view)                     # 复位视图
        add("Ctrl+F", self.board.fit_to_content)                 # 适应内容（一键总览整块板书）
        add("G", lambda: self._toggle_grid(not self.board.is_grid_enabled()))  # 网格开关
        add("H", lambda: self._toggle_highlighter(not self.board.highlighter))  # 荧光笔开关

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
        # 笔迹数/可撤销重做状态同时反映到状态栏（strokesChanged 会自动触发本方法）
        self._update_status()

    # -- 回调 --
    def _pick_color(self, hex_color):
        self.board.set_pen_color(hex_color)
        self._active_color = hex_color
        # 选色退出橡皮擦（橡皮与画笔颜色无关）；荧光笔保留——颜色即高亮色。
        # 据此同步两个工具栏勾选态。
        self._eraser_act.setChecked(False)
        self._hl_act.setChecked(self.board.highlighter)
        self._refresh_color_buttons()
        self._update_status()

    def _on_width(self, value):
        self.board.set_pen_width(value)
        self._update_status()

    def _toggle_eraser(self, checked):
        self.board.set_eraser(checked)
        # 让工具栏勾选态与实际状态自洽（无论经按钮点击还是程序调用都一致）
        self._eraser_act.setChecked(self.board.eraser)
        # 橡皮擦与荧光笔互斥：开启橡皮擦时取消荧光笔勾选
        self._hl_act.setChecked(self.board.highlighter)
        # 橡皮擦激活时取消颜色高亮，关闭时恢复
        self._refresh_color_buttons()
        self._update_status()

    def _toggle_highlighter(self, checked):
        """开启/关闭荧光笔（高亮笔），并同步工具栏勾选态与状态栏。"""
        self.board.set_highlighter(checked)
        self._hl_act.setChecked(self.board.highlighter)
        self._eraser_act.setChecked(self.board.eraser)
        self._refresh_color_buttons()
        self._update_status()

    def _toggle_grid(self, checked):
        """开启/关闭书写辅助网格，并同步工具栏勾选态与状态栏。"""
        self.board.set_grid_enabled(checked)
        self._grid_act.setChecked(self.board.is_grid_enabled())
        self._update_status()

    def _toggle_natural(self, checked):
        """开启/关闭自然笔（速度感知变宽），并同步工具栏勾选态与状态栏。"""
        self.board.set_variable_width(checked)
        self._natural_act.setChecked(self.board.variable_width)
        self._update_status()

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

    def _export(self):
        """将当前白板导出为 PNG 图片（供课堂分享 / 留存）。"""
        path, _ = QFileDialog.getSaveFileName(
            self, "导出白板图片", os.path.expanduser("~"), "PNG 图片 (*.png)"
        )
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        try:
            ok = self.board.export_image(path, "PNG")
        except Exception as e:  # noqa: BLE001 —— 捕获渲染/保存过程中的任何异常并提示
            QMessageBox.warning(self, "导出失败", str(e))
            return
        if not ok:
            QMessageBox.warning(self, "导出失败", "图片保存失败，请检查路径与权限。")

    def _ask_ai(self):
        """AI 教学联动入口：取当前白板快照作为上下文，向 AI 提问并展示答疑。"""
        from PySide6.QtWidgets import QInputDialog

        question, ok = QInputDialog.getText(self, "AI 教学答疑", "请输入你的问题：")
        if not ok or not question.strip():
            return
        board_dict = self.board.scene().to_dict()
        context = board_dict_to_text(board_dict)
        # 同时传入结构化白板，使本地答疑「板面感知」、远程请求带上 stats 上下文
        answer = self._ai.ask(question.strip(), context, board=board_dict)
        QMessageBox.information(
            self, "AI 教学答疑", answer + "\n\n--- 白板快照 ---\n" + context
        )

"""智学白板 SmartBoard —— 画笔光标环（书写精度反馈）。

兼容目标：Python 3.7 / PySide6 6.2.4。

为什么需要它：
课堂投影场景下，教师常远离屏幕或经低分辨率投影授课，很难凭肉眼预判
「下笔位置」与「笔迹粗细」。本模块在视口上叠加一个与当前画笔等大的透明圆环，
实时跟随鼠标，使下笔前的落点与笔宽一目了然，提升白板书写体验。

实现要点（仅用 6.2.4 公共 API）：
- 叠加层是挂在 viewport 上的 QWidget（不是 QGraphicsItem），因此不会被
  scene().render 卷入「导出图片」，天然排除，零侵入导出逻辑；
- 叠加层设置 WA_TransparentForMouseEvents，绝不会拦截鼠标事件，
  既有的鼠标书写 / 预览 / 右键擦除管线完全不受影响；
- 画笔模式：显示与画笔等宽、同色的实心环，预判落点与笔宽；
- 橡皮擦模式：显示与橡皮等宽的「红色虚线」环（set_eraser_style(True)），
  让教师直观看到橡皮擦除范围（橡皮以背景覆盖方式工作，本身无视觉反馈）；
- 荧光笔模式：隐藏环（其半透明叠加渲染与实心环的视觉预期不一致，避免误导）。
"""

from PySide6.QtCore import Qt, QPoint, QRect
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget


# 橡皮擦光标环采用醒目的红色虚线，与画笔实心环明显区分，避免与画笔环混淆。
ERASER_RING_COLOR = "#E53935"


class PenCursorOverlay(QWidget):
    """跟随鼠标、显示当前画笔/橡皮粗细与颜色的透明圆环（尺寸预览）。"""

    def __init__(self, parent=None):
        super(PenCursorOverlay, self).__init__(parent)
        # 透明背景，且不拦截鼠标事件（关键：避免破坏书写管线）
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._diameter = 4.0
        self._color = QColor("#1A1A1A")
        self._eraser = False   # 是否为橡皮擦模式（决定环的样式：虚线红环）
        self.hide()

    # -- 对外状态设置 --
    def set_pen(self, width, color):
        """设置圆环直径（= 画笔线宽，像素）与颜色。"""
        d = max(1.0, float(width))
        if d != self._diameter:
            self._diameter = d
            self.resize(int(d), int(d))
        self._color = QColor(color)
        self.update()

    def set_eraser_style(self, on):
        """切换橡皮擦样式：开启时环以红色虚线绘制（突出「擦除范围」）。"""
        on = bool(on)
        if on != self._eraser:
            self._eraser = on
            self.update()

    def is_eraser_style(self):
        return self._eraser

    def set_pen_color(self, color):
        """仅更新圆环颜色（线宽不变时避免无谓 resize）。"""
        self._color = QColor(color)
        self.update()

    def diameter(self):
        return self._diameter

    def pen_color(self):
        return self._color

    def move_to(self, pos):
        """把圆环中心对齐到视口局部坐标 pos（QPoint）。"""
        d = int(self._diameter)
        self.move(pos.x() - d // 2, pos.y() - d // 2)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect()
        # 描边线宽以「屏幕像素」计（cosmetic），不随任何缩放变化，
        # 保证不同笔宽下环的边框粗细一致、清晰可辨。
        # 橡皮擦模式用醒目红色虚线，与画笔实心环区分，直观提示擦除范围。
        if self._eraser:
            pen = QPen(QColor(ERASER_RING_COLOR), 1.5, Qt.DashLine)
        else:
            pen = QPen(self._color, 1.5, Qt.SolidLine)
        pen.setCosmetic(True)
        painter.setPen(pen)
        # 留 1px 边距，避免圆环被 widget 边界裁切
        margin = 1
        circle = QRect(
            rect.x() + margin, rect.y() + margin,
            rect.width() - 2 * margin, rect.height() - 2 * margin,
        )
        painter.drawEllipse(circle)
        # 中心十字，帮助精确定位落笔点
        cx, cy = rect.center().x(), rect.center().y()
        painter.drawLine(cx - 2, cy, cx + 2, cy)
        painter.drawLine(cx, cy - 2, cx, cy + 2)
        painter.end()

"""智学白板 SmartBoard —— 激光笔（演示指示点）。

兼容目标：Python 3.7 / PySide6 6.2.4。

教学场景：投影讲解时，教师常用一个红色发光点指示重点（PPT / 教学软件标配），
它只起「指示」作用，不应在板书上留下任何痕迹，讲完即消失。

实现要点（仅用 6.2.4 已具备的公共 API）：
- 叠加层是挂在 viewport 上的 QWidget（不是 QGraphicsItem），因此不会被
  scene().render 卷入「导出图片」，天然排除，对导出零侵入；
- WA_TransparentForMouseEvents + WA_TranslucentBackground：既不拦截鼠标事件
  （书写 / 精确擦除 / 中键平移管线完全不受影响），也不遮挡其下板书；
- 只在 set_active(True) 且鼠标位于视口内（由视图调用 show()）时可见；
  set_active(False) 会立即 hide 且 paintEvent 不绘制任何内容；
- 不产生 Stroke、不进入场景、不参与序列化 / 撤销，是纯视图层叠加物。
"""

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QPainter, QBrush, QRadialGradient
from PySide6.QtWidgets import QWidget


# 激光点默认颜色：醒目红色，投影 / 大屏下清晰可辨。
LASER_COLOR = "#FF1744"

# 光点整体直径（含光晕），屏幕像素。与画笔线宽无关——它是指示器，不是笔。
LASER_DIAMETER = 32.0


class LaserPointerOverlay(QWidget):
    """跟随鼠标的红色发光指示点（激光笔），仅在激活且鼠标在视口内时可见。"""

    def __init__(self, parent=None):
        super(LaserPointerOverlay, self).__init__(parent)
        # 透明背景 + 鼠标事件穿透：不拦截书写 / 擦除 / 平移，也不遮挡板书
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._active = False
        self._color = QColor(LASER_COLOR)
        self._diameter = float(LASER_DIAMETER)
        self.resize(int(self._diameter), int(self._diameter))
        self.hide()

    # -- 对外状态 --
    def set_active(self, on):
        """激活 / 关闭激光笔；关闭时立即隐藏，且不再绘制任何内容。"""
        on = bool(on)
        if on == self._active:
            return
        self._active = on
        if not on:
            self.hide()
        self.update()

    def is_active(self):
        return self._active

    def set_color(self, color):
        """设置激光点颜色（默认醒目红）；非法颜色被 QColor 安全忽略。"""
        c = QColor(color)
        if c.isValid() and c != self._color:
            self._color = c
            self.update()

    def color(self):
        return self._color

    def diameter(self):
        return self._diameter

    def move_to(self, pos):
        """把光点中心对齐到视口局部坐标 pos（QPoint）。"""
        d = int(self._diameter)
        self.move(pos.x() - d // 2, pos.y() - d // 2)

    # -- 绘制 --
    def paintEvent(self, event):
        if not self._active:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect()
        cx, cy = rect.center().x(), rect.center().y()
        radius = min(rect.width(), rect.height()) / 2.0 - 1.0
        if radius <= 0.0:
            painter.end()
            return

        # 光晕：径向渐变从「半透明本色」过渡到边缘「全透明」，营造发光观感。
        glow = QRadialGradient(cx, cy, radius)
        inner = QColor(self._color)
        inner.setAlphaF(0.55)
        outer = QColor(self._color)
        outer.setAlphaF(0.0)
        glow.setColorAt(0.0, inner)
        glow.setColorAt(1.0, outer)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(glow))
        painter.drawEllipse(QRectF(cx - radius, cy - radius,
                                   2.0 * radius, 2.0 * radius))

        # 中心聚焦亮斑：彩色实心点 + 白色内芯，模拟激光聚焦点。
        core = max(2.0, radius * 0.22)
        painter.setBrush(QBrush(self._color))
        painter.drawEllipse(QRectF(cx - core, cy - core, 2.0 * core, 2.0 * core))
        spark = max(1.0, core * 0.45)
        painter.setBrush(QBrush(QColor("#FFFFFF")))
        painter.drawEllipse(QRectF(cx - spark, cy - spark,
                                   2.0 * spark, 2.0 * spark))
        painter.end()

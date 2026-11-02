"""智学白板 SmartBoard —— 白板视图（鼠标交互 + 实时预览 + 图片导出）。

兼容目标：Python 3.7 / PySide6 6.2.4。
"""

import math

from PySide6.QtCore import Qt, QPoint, QPointF, QRectF
from PySide6.QtGui import QColor, QPen, QPainter, QPixmap, QTransform
from PySide6.QtWidgets import QGraphicsView, QGraphicsPathItem

from .whiteboard import Stroke, WhiteboardScene, simplify_points, SIMPLIFY_EPSILON
from .pen_cursor import PenCursorOverlay


class WhiteboardView(QGraphicsView):
    """把鼠标轨迹实时采集为 Stroke，并在抬起时提交到场景。"""

    def __init__(self, scene=None, parent=None):
        # 注意：QGraphicsView 不会取得 scene 的所有权，必须保留 Python 引用，
        # 否则构造参数离开作用域后 scene 的 C++ 对象会被回收，scene() 返回 None。
        if scene is None:
            scene = WhiteboardScene()
        self._scene = scene
        super(WhiteboardView, self).__init__(self._scene, parent)
        self.setRenderHint(QPainter.Antialiasing)
        self.setRenderHint(QPainter.SmoothPixmapTransform)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # 右键用于「精确擦除」，关闭默认右键菜单避免误弹
        self.setContextMenuPolicy(Qt.NoContextMenu)
        # 给一个足够大的画布，避免默认场景矩形过小
        self.setSceneRect(-2000, -2000, 4000, 4000)
        self.setBackgroundBrush(self.scene().backgroundBrush())

        self.pen_color = QColor("#1A1A1A")
        self.pen_width = 4
        self.eraser = False

        # 提交笔迹时的抽稀容差（像素）；设为 0 可关闭抽稀。
        # 默认沿用手写采样 1px 量级，保证视觉一致的前提下减少存储与渲染开销。
        self.simplify_epsilon = SIMPLIFY_EPSILON

        self._drawing = False
        self._current = None     # 正在绘制的 Stroke
        self._preview = None     # 实时预览的 QGraphicsPathItem
        self._mouse_inside = False  # 鼠标是否位于白板视口内（控制光标环显隐）

        # 画布漫游：滚轮以光标为锚缩放、中键拖拽平移（教学时放大细节 / 漫游大图）。
        # 这些只作用于「视图变换」，不改动场景数据、笔迹、预览环与导出，因此
        # 对既有书写、橡皮擦、序列化与 PNG 导出管线完全零侵入。
        self._panning = False
        self._pan_last = QPoint()

        # 画笔尺寸预览环：跟随鼠标、与当前画笔等大，帮助预判落点与笔宽。
        # 挂在 viewport 上（非场景图元），因此对导出图片零侵入。
        self.pen_cursor = PenCursorOverlay(self.viewport())
        self.pen_cursor.set_pen(self.pen_width, self.pen_color)

    # -- 画布缩放与平移（仅作用于视图变换，不触碰场景数据）--
    def zoomBy(self, factor, anchor_view=None):
        """以 anchor_view（视口坐标，默认视图中心）为锚点缩放 factor 倍。

        缩放后锚点对应的场景坐标保持在原视口位置，因此滚轮缩放会“聚焦”在光标处。
        实现上先以视图中心为锚 scale（保持中心场景点不动），再通过滚动条补偿把
        锚点的场景坐标拉回原位置；所有视图变换都不影响场景中的笔迹、预览环与导出。
        """
        if factor <= 0.0:
            return
        vp = self.viewport().rect()
        if anchor_view is None:
            anchor_view = vp.center()
        # 1) 记录锚点（视口坐标）当前对应的场景坐标
        scene_at_anchor = self.mapToScene(anchor_view)
        # 2) 以视图中心为锚缩放（默认 AnchorViewCenter 保持中心场景点不动）
        self.scale(factor, factor)
        # 3) 滚动条补偿：以中心为锚缩放会把锚点相对中心的像素偏移 d 放大 factor 倍，
        #    故把内容反向平移 d*(1-factor) 像素，使“光标处内容”保持不动（聚焦光标）。
        center = vp.center()
        dx = (anchor_view.x() - center.x()) * (1.0 - factor)
        dy = (anchor_view.y() - center.y()) * (1.0 - factor)
        self.horizontalScrollBar().setValue(
            self.horizontalScrollBar().value() - int(round(dx)))
        self.verticalScrollBar().setValue(
            self.verticalScrollBar().value() - int(round(dy)))

    def pan(self, dx_view, dy_view):
        """按屏幕像素位移平移画布（中键拖拽调用）。dx/dy 为视口像素增量。"""
        self.horizontalScrollBar().setValue(
            self.horizontalScrollBar().value() - int(round(dx_view)))
        self.verticalScrollBar().setValue(
            self.verticalScrollBar().value() - int(round(dy_view)))

    def reset_view(self):
        """复位视图：缩放归 1 并将原点居中（缩放/平移后一键还原）。"""
        self.setTransform(QTransform())
        self.centerOn(0.0, 0.0)

    def wheelEvent(self, event):
        """滚轮缩放：以光标为锚点平滑缩放（教学时放大细节、缩小总览）。"""
        delta = event.angleDelta().y()
        if delta == 0:
            super(WhiteboardView, self).wheelEvent(event)
            return
        # 平滑缩放：每 1/8 度对应一个接近 1 的底数，惯用滚轮步长（120）≈ 放大 20%
        factor = 1.0015 ** (-delta)
        self.zoomBy(factor, event.pos())
        event.accept()

    # -- 工具属性 --
    def set_pen_color(self, color):
        self.pen_color = QColor(color)
        self.eraser = False
        self.pen_cursor.set_pen_color(self.pen_color)

    def set_pen_width(self, width):
        self.pen_width = max(1, int(width))
        self.pen_cursor.set_pen(self.pen_width, self.pen_color)

    def set_eraser(self, on):
        self.eraser = bool(on)
        self._update_cursor_visibility()

    def _update_cursor_visibility(self):
        """仅在「画笔模式 + 鼠标位于视口内」时显示预览环，橡皮模式隐藏。"""
        if (not self.eraser) and self._mouse_inside:
            self.pen_cursor.show()
        else:
            self.pen_cursor.hide()

    # -- 鼠标事件 --
    def mousePressEvent(self, event):
        if event.button() == Qt.MiddleButton:
            # 中键：进入平移模式，不干扰左键书写 / 右键精确擦除
            self._panning = True
            self._pan_last = event.pos()
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        if event.button() == Qt.RightButton:
            # 右键：精确擦除光标下的整条笔迹（独立于当前画笔/橡皮工具）
            if not self._drawing:
                self._erase_stroke_at(event.pos())
            return
        if event.button() == Qt.LeftButton:
            self._drawing = True
            pos = self.mapToScene(event.pos())
            self._current = Stroke(self.pen_color, self.pen_width, [pos], self.eraser)
            self._start_preview()
        super(WhiteboardView, self).mousePressEvent(event)

    def mouseMoveEvent(self, event):
        # 预览环始终跟随鼠标（即使未落笔），让落点与笔宽在下笔前可见
        self.pen_cursor.move_to(event.pos())
        if self._panning:
            # 中键平移：把视口像素位移换算到场景坐标后平移视图
            delta = event.pos() - self._pan_last
            self._pan_last = event.pos()
            self.pan(delta.x(), delta.y())
            return
        if self._drawing and self._current is not None:
            pos = self.mapToScene(event.pos())
            last = self._current.points[-1]
            # 仅在移动超过 1px 时采样，减少冗余点、保持笔迹干净
            if (pos - last).manhattanLength() >= 1.0:
                self._current.points.append(pos)
                self._update_preview()
        super(WhiteboardView, self).mouseMoveEvent(event)

    def enterEvent(self, event):
        self._mouse_inside = True
        self._update_cursor_visibility()
        super(WhiteboardView, self).enterEvent(event)

    def leaveEvent(self, event):
        self._mouse_inside = False
        self.pen_cursor.hide()
        super(WhiteboardView, self).leaveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._panning and event.button() == Qt.MiddleButton:
            # 结束平移，恢复指针光标
            self._panning = False
            self.setCursor(Qt.ArrowCursor)
            event.accept()
            return
        if self._drawing and event.button() == Qt.LeftButton:
            self._drawing = False
            self._finish_preview()
            self._commit_current()
        super(WhiteboardView, self).mouseReleaseEvent(event)

    def _commit_current(self):
        """抬起笔时：把当前笔迹抽稀（减小存储）后提交到场景。

        抽稀仅作用于提交给场景/序列化的点序列，预览阶段仍使用完整采样点，
        因此书写手感不变，成品笔迹在 epsilon 容差内与原轨迹视觉一致。
        """
        if self._current is None:
            return
        if len(self._current.points) >= 3 and self.simplify_epsilon > 0:
            self._current.points = simplify_points(
                self._current.points, self.simplify_epsilon
            )
        if len(self._current.points) >= 1:
            self.scene().add_stroke(self._current)
        self._current = None

    # -- 精确擦除（右键删除整条笔迹）--
    def _erase_stroke_at(self, screen_pos):
        self._erase_stroke_at_scene(self.mapToScene(screen_pos))

    def _erase_stroke_at_scene(self, scene_pos):
        """删除场景坐标命中的整条笔迹；命中空白处则无操作。"""
        item = self.scene().itemAt(scene_pos, self.transform())
        if item is None or item is self._preview:
            return False
        stroke = self.scene()._stroke_of.get(item)
        if stroke is None:
            return False
        return self.scene().remove_stroke(stroke)

    # -- 预览管理 --
    def _preview_pen(self):
        bg = self.scene().backgroundBrush().color()
        color = bg if self._current.eraser else self._current.color
        return QPen(color, self._current.width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)

    def _start_preview(self):
        if self._preview is not None:
            self.scene().removeItem(self._preview)
        self._preview = QGraphicsPathItem()
        self._preview.setPen(self._preview_pen())
        self._preview.setZValue(1)
        self.scene().addItem(self._preview)
        self._update_preview()

    def _update_preview(self):
        if self._preview is None or self._current is None:
            return
        self._preview.setPath(self._current.to_path(smooth=True))

    def _finish_preview(self):
        if self._preview is not None:
            self.scene().removeItem(self._preview)
            self._preview = None

    # -- 图片导出 --
    def content_rect(self, padding=24.0):
        """返回刚好包围所有笔迹的内容矩形（含 padding 留白）。

        无笔迹时回退到一个默认画布，避免导出一张 4000×4000 的巨幅空白图。
        """
        if self.scene().count() == 0:
            return QRectF(-100.0, -100.0, 200.0, 200.0)
        return self.scene().itemsBoundingRect().adjusted(
            -padding, -padding, padding, padding
        )

    def export_image(self, path, fmt="PNG"):
        """将当前白板渲染为图片并保存到 path，返回是否保存成功。

        设计要点（均只使用 PySide6 6.2.4 已具备的 QPixmap/QPainter/Scene.render）：
        - 仅渲染「笔迹内容区域 + 留白」，而非默认 4000×4000 巨幅画布，文件体积可控；
        - 先以场景背景色填充整张图，再渲染笔迹（平滑路径），保证所见即所得；
        - 采用 IgnoreAspectRatio，规避尺寸取整带来的轻微比例偏差导致留白不均。
        """
        rect = self.content_rect()
        width = max(1, int(math.ceil(rect.width())))
        height = max(1, int(math.ceil(rect.height())))

        pixmap = QPixmap(width, height)
        bg = self.scene().backgroundBrush().color()
        pixmap.fill(bg)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        target = QRectF(0.0, 0.0, float(width), float(height))
        self.scene().render(painter, target, rect, Qt.IgnoreAspectRatio)
        painter.end()

        return pixmap.save(path, fmt)

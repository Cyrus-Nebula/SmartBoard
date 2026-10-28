"""智学白板 SmartBoard —— 白板核心模块。

兼容目标：Python 3.7 / PySide6 6.2.4。
本模块只依赖 PySide6 公共 API（6.2.4 已具备），不触碰任何新版独有特性。
"""

import json

from PySide6.QtCore import Qt, QPointF, Signal
from PySide6.QtGui import QColor, QPen, QBrush, QPainter, QPainterPath
from PySide6.QtWidgets import QGraphicsScene, QGraphicsPathItem


# ---------------------------------------------------------------------------
# 路径构造：折线 vs 二次贝塞尔平滑
# ---------------------------------------------------------------------------

def build_polyline(points):
    """构造未经平滑的折线路径（用于对照与序列化回放）。"""
    path = QPainterPath()
    if not points:
        return path
    path.moveTo(points[0])
    for p in points[1:]:
        path.lineTo(p)
    return path


# 提交笔迹时抽稀的默认容差（像素）。RDP 会保留所有偏离首尾连线超过该值的顶点，
# 视觉上与原笔迹几乎无差别，但能显著减少存储点数与渲染/载入开销。
SIMPLIFY_EPSILON = 1.5


def _perp_distance(point, a, b):
    """点 point 到线段 a→b 的垂直距离；a==b（零长线段）时退化为到点的距离。

    纯 Python 实现，不依赖 numpy，兼容 Python 3.7。
    """
    dx = b.x() - a.x()
    dy = b.y() - a.y()
    norm = (dx * dx + dy * dy) ** 0.5
    if norm == 0.0:
        return ((point.x() - a.x()) ** 2 + (point.y() - a.y()) ** 2) ** 0.5
    # 点到直线距离公式（线段端点已知，标准 RDP 假设点落在线段投影范围内）
    return abs(dy * point.x() - dx * point.y() + b.x() * a.y() - b.y() * a.x()) / norm


def simplify_points(points, epsilon=SIMPLIFY_EPSILON):
    """用 Ramer–Douglas–Peucker 算法对采样点做抽稀，保留轮廓关键顶点。

    - 端点必然保留，保证笔迹首尾位置不变。
    - 递归拆分的迭代实现，避免深曲线导致 Python 递归深度超限。
    - 点数 <= 2 或 epsilon <= 0 时直接返回副本（不抽稀），保证安全与确定性。
    返回新的 QPointF 列表，不修改入参。
    """
    if epsilon <= 0 or len(points) <= 2:
        return [QPointF(p) for p in points]

    pts = [QPointF(p) for p in points]
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        start, end = stack.pop()
        a, b = pts[start], pts[end]
        dmax, index = 0.0, -1
        for i in range(start + 1, end):
            d = _perp_distance(pts[i], a, b)
            if d > dmax:
                dmax, index = d, i
        if dmax > epsilon and index != -1:
            keep[index] = True
            stack.append((start, index))
            stack.append((index, end))
    return [p for i, p in enumerate(pts) if keep[i]]


def build_smooth_path(points):
    """用「中点 + 二次贝塞尔」对采样点做平滑，得到自然的书写笔迹。

    算法：以相邻两点的中点为段终点、以原采样点为控制点，逐段 quadTo。
    这样曲线会逼近每个采样点但圆滑拐角，避免手写时的锯齿感。
    单点绘制成一个小圆点；两点直接连线。
    """
    path = QPainterPath()
    if not points:
        return path
    if len(points) == 1:
        # 单击落点：画一个极短线段，配合 RoundCap 呈现为圆点
        p = points[0]
        path.moveTo(p)
        path.lineTo(p.x() + 0.01, p.y() + 0.01)
        return path
    if len(points) == 2:
        path.moveTo(points[0])
        path.lineTo(points[1])
        return path
    path.moveTo(points[0])
    for i in range(1, len(points) - 1):
        ctrl = points[i]
        end = (points[i] + points[i + 1]) / 2.0
        path.quadTo(ctrl, end)
    path.lineTo(points[-1])
    return path


# ---------------------------------------------------------------------------
# 笔迹数据模型
# ---------------------------------------------------------------------------

class Stroke:
    """一条笔迹：颜色、线宽、采样点序列、是否为橡皮擦。"""

    def __init__(self, color, width, points, eraser=False):
        # type: (QColor, int, list, bool) -> None
        self.color = QColor(color)
        self.width = int(width)
        self.points = [QPointF(p) for p in points]
        self.eraser = bool(eraser)

    def to_path(self, smooth=True):
        if smooth:
            return build_smooth_path(self.points)
        return build_polyline(self.points)

    def to_dict(self):
        return {
            "color": self.color.name(),  # 形如 "#1a1a1a"
            "width": self.width,
            "eraser": self.eraser,
            "points": [[round(p.x(), 3), round(p.y(), 3)] for p in self.points],
        }

    @classmethod
    def from_dict(cls, d):
        pts = [QPointF(x, y) for x, y in d.get("points", [])]
        return cls(d.get("color", "#000000"), d.get("width", 4), pts, d.get("eraser", False))


# ---------------------------------------------------------------------------
# 白板场景：管理笔迹、撤销/重做、序列化
# ---------------------------------------------------------------------------

class WhiteboardScene(QGraphicsScene):
    """以 QGraphicsScene 承载笔迹，每条 Stroke 对应一个 QGraphicsPathItem。"""

    # 笔迹集合发生变化（增/删/清空/载入）时发出，供界面联动刷新按钮可用态
    strokesChanged = Signal()

    def __init__(self, background=None):
        super(WhiteboardScene, self).__init__()
        self._bg = QColor(background) if background is not None else QColor("#FFFFFF")
        self.setBackgroundBrush(QBrush(self._bg))
        self._strokes = []                 # 已提交笔迹（绘制顺序）
        self._items = {}                   # Stroke -> QGraphicsPathItem
        self._stroke_of = {}               # QGraphicsPathItem -> Stroke（反向索引）
        self._redo = []                    # 撤销栈
        self._z_counter = 0                # 单调递增 z 计数器（决定层叠顺序）

    # -- 公开访问器（便于测试与外部读取）--
    def count(self):
        return len(self._strokes)

    def can_undo(self):
        return len(self._strokes) > 0

    def can_redo(self):
        return len(self._redo) > 0

    # -- 内部：把一条 Stroke 渲染并挂到场景 --
    def _attach(self, stroke):
        item = QGraphicsPathItem()
        item.setPath(stroke.to_path(smooth=True))
        pen_color = self._bg if stroke.eraser else stroke.color
        pen = QPen(pen_color, stroke.width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        item.setPen(pen)
        # 按「绘制顺序」分配单调递增 z：橡皮擦与普通笔迹一视同仁。
        # 这样橡皮只遮住它“之前”绘制的笔迹，而不会压住“之后”新画的笔迹，
        # 修复旧实现固定 eraser z=2 导致后画笔迹被橡皮区域遮挡的缺陷。
        if getattr(stroke, "_z", None) is None:
            stroke._z = self._z_counter
            self._z_counter += 1
        item.setZValue(stroke._z)
        self.addItem(item)
        self._strokes.append(stroke)
        self._items[stroke] = item
        self._stroke_of[item] = stroke
        return stroke

    def _detach(self, stroke):
        """把一条 Stroke 的图元从场景摘下，并清理双向索引。"""
        item = self._items.pop(stroke, None)
        if item is not None:
            self._stroke_of.pop(item, None)
            self.removeItem(item)

    def add_stroke(self, stroke):
        """提交一条新笔迹；任何新操作都会清空重做栈。"""
        self._attach(stroke)
        self._redo = []
        self.strokesChanged.emit()
        return stroke

    def undo(self):
        if not self._strokes:
            return False
        s = self._strokes.pop()
        self._detach(s)
        self._redo.append(s)
        self.strokesChanged.emit()
        return True

    def remove_stroke(self, stroke):
        """删除指定笔迹（整条移除），可用于右键精确擦除。

        被删笔迹进入重做栈，因此可用 Ctrl+Shift+Z / Ctrl+Y 恢复，
        契合「书写纠错」场景。新绘制会像往常一样清空重做栈。
        """
        if stroke not in self._strokes:
            return False
        self._strokes.remove(stroke)
        self._detach(stroke)
        self._redo.append(stroke)
        self.strokesChanged.emit()
        return True

    def redo(self):
        if not self._redo:
            return False
        s = self._redo.pop()
        self._attach(s)
        self.strokesChanged.emit()
        return True

    def clear_all(self):
        self.clear()
        self._strokes = []
        self._items = {}
        self._stroke_of = {}
        self._redo = []
        self._z_counter = 0
        self.strokesChanged.emit()

    # -- 序列化 --
    def to_dict(self):
        return {"version": 1, "strokes": [s.to_dict() for s in self._strokes]}

    def to_json(self):
        return json.dumps(self.to_dict(), ensure_ascii=False)

    def load_dict(self, data):
        """用 dict 覆盖当前内容（用于载入文件）。"""
        self.clear_all()
        for sd in data.get("strokes", []):
            self._attach(Stroke.from_dict(sd))
        # clear_all 已置空并发射过一次，这里在所有笔迹挂上后再发一次，
        # 保证界面在「载入」操作后拿到最终正确状态。
        self.strokesChanged.emit()
        return self

    @classmethod
    def from_dict(cls, data, background=None):
        scene = cls(background)
        scene.load_dict(data)
        return scene

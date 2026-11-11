"""智学白板 SmartBoard —— 白板核心模块。

兼容目标：Python 3.7 / PySide6 6.2.4。
本模块只依赖 PySide6 公共 API（6.2.4 已具备），不触碰任何新版独有特性。
"""

import json
import math

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

# 荧光笔（高亮笔）的渲染不透明度：半透明叠加，强调关键内容而不遮挡其下文字。
# 取值 0~1，越小越透明。仅作用于荧光笔笔迹，对普通笔迹/橡皮擦/序列化零侵入。
HL_ALPHA = 0.4


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
# 自然笔（速度感知变宽）：让笔迹随书写速度变化粗细，模拟真实钢笔/毛笔手感。
# 设计要点（兼容 Python 3.7 / PySide6 6.2.4，纯标准库 + Qt 公共 API）：
# - 速度→宽度映射是纯函数，不依赖 GUI，可单元覆盖、确定可复现；
# - 变宽笔迹用「中心线偏移成闭合轮廓 + 填充」渲染（而非恒定宽度的 QPen），
#   这是变宽描边的标准做法，对既有固定宽度笔迹、橡皮擦、序列化、导出零侵入；
# - 该特性默认关闭（工具栏「自然笔」开关），因此不改写任何既有默认行为，
#   老白板文件（无 half_widths）载入后仍是原来的固定宽度笔迹。
# ---------------------------------------------------------------------------

def compute_half_widths(points, times, base_width, min_factor=0.45,
                        max_factor=1.0, soft=1.2, smooth_widths=True):
    """根据采样点的时间戳估算每一点的半宽（场景坐标，px），实现"慢笔粗、快笔细"。

    - points：QPointF 采样点（场景坐标）；
    - times：与 points 等长的时间戳（毫秒，如 QInputEvent.timestamp()）；
    - base_width：画笔基准线宽（场景坐标，px）；
    - 速度 speed = 相邻点距离 / 时间间隔（px/ms），越慢 speed 越小；
    - 因子 factor = max_factor - (max_factor - min_factor) * speed/(speed+soft)：
      静止(speed=0)→factor=max_factor（最粗）；极快→factor→min_factor（最细）；
      soft 为半饱和常数（px/ms），决定"多快开始变细"。
    - 返回与 points 等长的半宽列表（= base_width*factor/2），可直接送入轮廓构造。
    纯函数、无副作用，便于单元验证「慢→更粗、快→更细」的单调性。
    """
    n = len(points)
    if n == 0:
        return []
    hw = [0.0] * n
    prev_speed = 0.0
    for i in range(n):
        if i == 0:
            speed = 0.0
        else:
            dt = times[i] - times[i - 1]   # 毫秒
            if dt <= 0:
                speed = prev_speed          # 时间为 0/负：沿用上一速度，避免除零
            else:
                dx = points[i].x() - points[i - 1].x()
                dy = points[i].y() - points[i - 1].y()
                dist = (dx * dx + dy * dy) ** 0.5
                speed = dist / dt           # px/ms
        prev_speed = speed
        factor = max_factor - (max_factor - min_factor) * (speed / (speed + soft))
        hw[i] = (float(base_width) * factor) / 2.0
    if smooth_widths and n >= 3:
        hw = _moving_avg(hw, 1)
    return hw


def _moving_avg(values, radius):
    """对列表做半径为 radius 的滑动平均（端点夹到边界），平滑宽度抖动。"""
    n = len(values)
    out = [0.0] * n
    for i in range(n):
        lo = max(0, i - radius)
        hi = min(n - 1, i + radius)
        total = 0.0
        count = 0
        for j in range(lo, hi + 1):
            total += values[j]
            count += 1
        out[i] = total / count
    return out


def build_variable_width_path(center, half_widths):
    """把「中心线 + 逐点半宽」构造为可填充的闭合轮廓路径（变宽描边）。

    对每个顶点沿其局部切线法线方向左右各偏移半宽，得到 left/right 两条边，
    首尾连成闭合多边形后用画刷填充，即得随速度变化粗细的笔迹。
    - 单点：退化为一个填充圆点（落笔点）；
    - 两点及以上：左右偏移轮廓；
    - half_widths 与 center 长度不一致或为空时安全退化为折线，绝不抛异常。
    """
    path = QPainterPath()
    n = len(center)
    if n == 0:
        return path
    if len(half_widths) != n:
        return build_polyline(center)
    if n == 1:
        r = max(0.1, half_widths[0])
        c = center[0]
        path.addEllipse(c.x() - r, c.y() - r, 2.0 * r, 2.0 * r)
        return path
    left = []
    right = []
    for i in range(n):
        if i == 0:
            t = center[1] - center[0]
        elif i == n - 1:
            t = center[n - 1] - center[n - 2]
        else:
            t = center[i + 1] - center[i - 1]
        tx, ty = t.x(), t.y()
        length = (tx * tx + ty * ty) ** 0.5
        if length == 0.0:
            nx, ny = 1.0, 0.0
        else:
            nx, ny = -ty / length, tx / length
        hw = max(0.0, half_widths[i])
        left.append(QPointF(center[i].x() + nx * hw, center[i].y() + ny * hw))
        right.append(QPointF(center[i].x() - nx * hw, center[i].y() - ny * hw))
    path.moveTo(left[0])
    for p in left[1:]:
        path.lineTo(p)
    for p in reversed(right):
        path.lineTo(p)
    path.closeSubpath()
    return path


def simplify_with_meta(points, meta, epsilon=SIMPLIFY_EPSILON):
    """与 simplify_points 等价的 RDP 抽稀，但同步保留并抽掉并行的 meta（如时间戳）。

    返回 (保留点列表, 保留 meta 列表)。点数 <= 2 或 epsilon <= 0 时原样返回副本，
    保证确定性；用于变宽笔迹提交时让「点」与「时间戳」保持一一对应。
    """
    if epsilon <= 0 or len(points) <= 2:
        return [QPointF(p) for p in points], (list(meta) if meta is not None else None)
    pts = [QPointF(p) for p in points]
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    meta_kept = (list(meta) if meta is not None else None)
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
    kept_pts = [p for i, p in enumerate(pts) if keep[i]]
    kept_meta = [meta_kept[i] for i in range(len(pts)) if keep[i]] if meta_kept is not None else None
    return kept_pts, kept_meta


# ---------------------------------------------------------------------------
# 笔迹数据模型
# ---------------------------------------------------------------------------

class Stroke:
    """一条笔迹：颜色、线宽、采样点序列、是否为橡皮擦、是否为荧光笔。

    可选 half_widths：与 points 等长的逐点半宽列表（场景坐标，px），用于「自然笔」
    速度感知变宽。为 None 时退化为固定宽度（与旧行为一致），保证老文件向后兼容。
    highlighter：荧光笔标志，开启时笔迹以 HL_ALPHA 半透明度渲染（叠加高亮），
    其余语义与普通笔迹完全一致，对橡皮擦/序列化/导出零侵入。
    """

    def __init__(self, color, width, points, eraser=False, half_widths=None,
                 highlighter=False):
        # type: (QColor, int, list, bool, list, bool) -> None
        self.color = QColor(color)
        self.width = int(width)
        self.points = [QPointF(p) for p in points]
        self.eraser = bool(eraser)
        self.highlighter = bool(highlighter)
        # 仅当长度匹配 points 时才视为有效变宽笔迹，避免脏数据导致渲染异常
        if half_widths is not None and len(half_widths) == len(self.points):
            self._half_widths = [float(h) for h in half_widths]
        else:
            self._half_widths = None

    def uses_variable_width(self):
        """是否为速度感知变宽笔迹（决定用填充轮廓渲染还是恒定宽度 QPen）。"""
        return (
            self._half_widths is not None
            and len(self._half_widths) == len(self.points)
            and len(self.points) > 0
        )

    def to_path(self, smooth=True):
        if self.uses_variable_width():
            return build_variable_width_path(self.points, self._half_widths)
        if smooth:
            return build_smooth_path(self.points)
        return build_polyline(self.points)

    def to_dict(self):
        d = {
            "color": self.color.name(),  # 形如 "#1a1a1a"
            "width": self.width,
            "eraser": self.eraser,
            "points": [[round(p.x(), 3), round(p.y(), 3)] for p in self.points],
        }
        # 变宽笔迹序列化逐点半宽（场景坐标，3 位小数）；固定宽度笔迹省略以兼容旧文件
        if self._half_widths is not None:
            d["half_widths"] = [round(h, 3) for h in self._half_widths]
        # 荧光笔标志仅在开启时写入，默认（关闭）省略以兼容旧文件
        if self.highlighter:
            d["highlighter"] = True
        return d

    @classmethod
    def from_dict(cls, d):
        pts = [QPointF(x, y) for x, y in d.get("points", [])]
        hw = d.get("half_widths")
        if hw:
            hw = [float(x) for x in hw]
        return cls(
            d.get("color", "#000000"),
            d.get("width", 4),
            pts,
            d.get("eraser", False),
            half_widths=hw,
            highlighter=d.get("highlighter", False),
        )


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

        # 清空/载入覆盖前的笔迹快照，用于「撤销清空 / 撤销载入」整批恢复，
        # 杜绝 Ctrl+L 误清或误载后板书无法恢复的数据丢失风险（与既有逐条撤销互补）。
        self._cleared = []                 # 被清空/覆盖的笔迹（绘制顺序）
        self._cleared_z = 0                # 清空/覆盖前的 z 计数器，恢复后继续递增

        # 书写辅助网格（默认关闭，对既有书写/橡皮擦/序列化/导出零侵入）
        self._grid_enabled = False
        self._grid_size = 40.0             # 网格间距（场景坐标，px）
        self._grid_color = QColor("#E6E6E6")  # 极淡灰，投影下不喧宾夺主

    # -- 公开访问器（便于测试与外部读取）--
    def count(self):
        return len(self._strokes)

    def can_undo(self):
        # 有笔迹可逐条撤销，或刚被清空/载入覆盖的板面可整批恢复（Ctrl+Z 防误清）
        return len(self._strokes) > 0 or len(self._cleared) > 0

    def can_redo(self):
        return len(self._redo) > 0

    # -- 内部：把一条 Stroke 渲染并挂到场景 --
    def _attach(self, stroke):
        item = QGraphicsPathItem()
        item.setPath(stroke.to_path(smooth=True))
        if stroke.uses_variable_width():
            # 变宽笔迹：用「填充轮廓」渲染（画刷着色、无描边），
            # 橡皮擦模式则用背景色填充以"擦除"其下方笔迹。
            color = self._bg if stroke.eraser else stroke.color
            item.setBrush(QBrush(color))
            item.setPen(Qt.NoPen)
        else:
            pen_color = self._bg if stroke.eraser else stroke.color
            pen = QPen(pen_color, stroke.width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
            item.setPen(pen)
        # 荧光笔：半透明叠加渲染（HL_ALPHA），强调关键内容而不遮挡其下文字。
        # 与橡皮擦互斥（橡皮以背景色覆盖，必须不透明），故仅在「非橡皮」时生效。
        if stroke.highlighter and not stroke.eraser:
            item.setOpacity(HL_ALPHA)
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
        if self._strokes:
            # 逐条撤销：取最近一笔，摘下并放入重做栈
            s = self._strokes.pop()
            self._detach(s)
            self._redo.append(s)
            self.strokesChanged.emit()
            return True
        if self._cleared:
            # 撤销「清空 / 载入覆盖」：整批恢复被清掉的笔迹（保留各自 z），
            # 先清掉场景里现有的图元（含载入/新画内容），再以原 z 挂回。
            self.clear()
            restored = self._cleared
            z = self._cleared_z
            self._cleared = []
            self._strokes = []
            self._items = {}
            self._stroke_of = {}
            self._z_counter = z
            for s in restored:
                self._attach(s)   # 已提交笔迹必有 _z，不会重新分配
            self.strokesChanged.emit()
            return True
        return False

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
        if not self._strokes:
            return
        # 快照当前笔迹，使「清空」可被 Ctrl+Z 整批撤销（防误清数据丢失）。
        # 载入覆盖（load_dict）也会走到这里，因此误载同样可一键还原。
        self._cleared = self._strokes[:]
        self._cleared_z = self._z_counter
        self.clear()
        self._strokes = []
        self._items = {}
        self._stroke_of = {}
        self._redo = []
        self._z_counter = 0
        self.strokesChanged.emit()

    # -- 书写辅助网格 --
    def set_grid_enabled(self, on):
        """开关淡淡的书写辅助网格：帮助对齐与保持书写水平。

        网格画在场景背景层，因此视图与 PNG 导出均「所见即所得」地包含它；
        但默认关闭，对既有书写、橡皮擦、序列化与导出逻辑零侵入。
        """
        self._grid_enabled = bool(on)
        self.update()  # 触发背景重绘（含网格），无网格时仅是一次空重绘

    def is_grid_enabled(self):
        return self._grid_enabled

    def set_grid_size(self, size):
        """设置网格间距（场景坐标，单位 px）；过小的间距会被夹到 4px 以避免过密。"""
        self._grid_size = max(4.0, float(size))
        if self._grid_enabled:
            self.update()

    def grid_size(self):
        return self._grid_size

    def drawBackground(self, painter, rect):
        # 先绘制纯色背景（保留默认行为，保证橡皮/导出底色一致），再叠加可选网格
        super(WhiteboardScene, self).drawBackground(painter, rect)
        if not self._grid_enabled:
            return
        step = self._grid_size
        if step <= 0:
            return
        pen = QPen(self._grid_color)
        # 网格线宽以「屏幕像素」计（cosmetic），任意缩放下保持纤细一致
        pen.setCosmetic(True)
        painter.save()
        painter.setPen(pen)
        x = math.floor(rect.left() / step) * step
        while x <= rect.right():
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            x += step
        y = math.floor(rect.top() / step) * step
        while y <= rect.bottom():
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            y += step
        painter.restore()

    # -- 序列化 --
    def to_dict(self):
        return {"version": 1, "strokes": [s.to_dict() for s in self._strokes]}

    def to_json(self):
        return json.dumps(self.to_dict(), ensure_ascii=False)

    def load_dict(self, data):
        """用 dict 覆盖当前内容（用于载入文件）。

        原子性保证：先完整解析并校验所有笔迹，只有全部成功后才替换场景内容。
        任何一条笔迹格式非法都会整体失败并抛出 ValueError，且**不会清空**当前白板——
        避免「载入损坏文件导致正在编辑的内容被抹掉且无法恢复」的数据丢失风险。
        仅当全部解析通过，才执行 clear + attach，与旧行为一致。
        """
        if not isinstance(data, dict):
            raise ValueError("白板文件格式无效：根节点应为 JSON 对象")
        raw = data.get("strokes", []) or []
        parsed = []
        for idx, sd in enumerate(raw):
            if not isinstance(sd, dict):
                raise ValueError(
                    "白板文件格式无效：第 %d 条笔迹不是 JSON 对象" % (idx + 1)
                )
            try:
                parsed.append(Stroke.from_dict(sd))
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                raise ValueError(
                    "白板文件格式无效：第 %d 条笔迹解析失败（%s）" % (idx + 1, exc)
                )
        # 全部解析成功后再替换场景内容，确保「半途失败不丢当前白板」
        self.clear_all()
        for s in parsed:
            self._attach(s)
        # 载入是「显式替换」操作（损坏文件已被原子性保护拦截），不把它变成
        # 两步撤销（先删载入内容、再还原旧板面），故清空「可撤销清空」快照，
        # 让载入成为干净的基线；撤销仍按逐条作用于载入的笔迹。
        self._cleared = []
        self._cleared_z = 0
        # clear_all 已置空并发射过一次，这里在所有笔迹挂上后再发一次，
        # 保证界面在「载入」操作后拿到最终正确状态。
        self.strokesChanged.emit()
        return self

    @classmethod
    def from_dict(cls, data, background=None):
        scene = cls(background)
        scene.load_dict(data)
        return scene

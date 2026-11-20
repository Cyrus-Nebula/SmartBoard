"""智学白板 SmartBoard —— SVG 矢量导出（纯逻辑，兼容 Python 3.7 / PySide6 6.2.4）。

设计要点：
- 笔迹本质是矢量路径（QPainterPath），SVG 直接复用同一路径，导出结果与屏幕
  所见、PNG 导出同源，可无限缩放不失真，便于插入课件 / 论文 / 网页或二次编辑；
- 按绘制顺序输出：橡皮擦以「背景色覆盖」表达（与场景 _attach / PNG 导出语义
  一致），荧光笔以 stroke-opacity / fill-opacity 表达，变宽笔迹以填充轮廓表达；
- 只依赖标准库 + Qt 公共 API，不依赖 GUI 事件循环，便于 headless 单元测试；
- 只读取场景数据，不改动书写、撤销、序列化与 PNG 导出，零侵入。
"""

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QPainterPath

from .whiteboard import HL_ALPHA, build_variable_width_path


def _fmt(value, precision=2):
    """把数值格式化为精简 SVG 数字（去掉无意义的 0 与负零）。"""
    text = ("%.*f" % (precision, float(value))).rstrip("0").rstrip(".")
    if text in ("", "-0"):
        return "0"
    return text


def path_to_d(path, precision=2):
    """把 QPainterPath 转为 SVG path 的 d 属性字符串。

    Qt 会把 quadTo 折算为三次贝塞尔（CurveToElement + 两个 CurveToDataElement），
    closeSubpath 追加一条回到起点的线段；这里按元素序列原样翻译，得到与实际
    渲染完全一致的矢量路径。孤立的 CurveToDataElement（理论上不会单独出现）跳过。
    """
    parts = []
    i = 0
    count = path.elementCount()
    while i < count:
        elem = path.elementAt(i)
        etype = elem.type
        if etype == QPainterPath.MoveToElement:
            parts.append("M %s %s" % (_fmt(elem.x, precision), _fmt(elem.y, precision)))
            i += 1
        elif etype == QPainterPath.LineToElement:
            parts.append("L %s %s" % (_fmt(elem.x, precision), _fmt(elem.y, precision)))
            i += 1
        elif etype == QPainterPath.CurveToElement:
            # 三次贝塞尔：elem 为控制点 1，随后两个 CurveToDataElement 为控制点 2 与终点
            c1x, c1y = elem.x, elem.y
            c2x = c2y = ex = ey = 0.0
            if i + 1 < count and path.elementAt(i + 1).type == QPainterPath.CurveToDataElement:
                c2 = path.elementAt(i + 1)
                c2x, c2y = c2.x, c2.y
            if i + 2 < count and path.elementAt(i + 2).type == QPainterPath.CurveToDataElement:
                end = path.elementAt(i + 2)
                ex, ey = end.x, end.y
            parts.append("C %s %s %s %s %s %s" % (
                _fmt(c1x, precision), _fmt(c1y, precision),
                _fmt(c2x, precision), _fmt(c2y, precision),
                _fmt(ex, precision), _fmt(ey, precision),
            ))
            i += 3
        else:
            i += 1
    return " ".join(parts)


def scene_to_svg(scene, background=None, padding=24.0, precision=2):
    """把场景笔迹导出为完整的 SVG 文档字符串。

    - background：显式背景色；缺省取 scene.backgroundBrush().color()；
    - padding：内容边界外的留白（与 view.content_rect 默认 24 一致）；
    - 空板回退到 -100,-100,200,200 画布（与 view 的 PNG 导出口径一致）；
    - 非法 padding 安全回退为默认 24，负 padding 回退为 0。
    """
    bg = QColor(background) if background is not None else scene.backgroundBrush().color()
    bg_hex = bg.name()

    rect = scene.content_bounding_rect()
    if rect.isNull() or rect.isEmpty():
        rect = QRectF(-100.0, -100.0, 200.0, 200.0)
    try:
        padding = float(padding)
    except (TypeError, ValueError):
        padding = 24.0
    if padding < 0:
        padding = 0.0
    rect = rect.adjusted(-padding, -padding, padding, padding)

    width = max(1, int(round(rect.width())))
    height = max(1, int(round(rect.height())))
    x, y, w, h = _fmt(rect.x(), precision), _fmt(rect.y(), precision), \
        _fmt(rect.width(), precision), _fmt(rect.height(), precision)

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="%s %s %s %s">'
        % (width, height, x, y, w, h),
        '  <rect x="%s" y="%s" width="%s" height="%s" fill="%s"/>' % (x, y, w, h, bg_hex),
    ]

    for stroke in scene._strokes:
        color = bg_hex if stroke.eraser else stroke.color.name()
        # 荧光笔半透明；橡皮擦以背景色覆盖，必须不透明（与渲染一致）
        opacity = None if (stroke.eraser or not stroke.highlighter) else HL_ALPHA
        if stroke.uses_variable_width():
            outline = build_variable_width_path(stroke.points, stroke._half_widths)
            d = path_to_d(outline, precision) + " Z"
            fill_op = ' fill-opacity="%s"' % _fmt(opacity, 3) if opacity is not None else ""
            lines.append('  <path d="%s" fill="%s" stroke="none"%s/>' % (d, color, fill_op))
        else:
            d = path_to_d(stroke.to_path(smooth=True), precision)
            stroke_op = ' stroke-opacity="%s"' % _fmt(opacity, 3) if opacity is not None else ""
            lines.append(
                '  <path d="%s" fill="none" stroke="%s" stroke-width="%d" '
                'stroke-linecap="round" stroke-linejoin="round"%s/>'
                % (d, color, stroke.width, stroke_op)
            )
    lines.append("</svg>")
    return "\n".join(lines) + "\n"

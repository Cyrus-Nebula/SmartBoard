# 智学白板 SmartBoard

基于 **PySide6** 的多功能教学白板（智学白板）。支持手写笔迹平滑、橡皮擦、
撤销/重做、清空、白板文件保存与载入。

## 兼容目标

- Python 3.7+
- PySide6 6.2.4+

## 安装

```bash
pip install -r requirements.txt
```

## 运行

```bash
python main.py
```

## 功能

- **平滑书写**：采用「中点 + 二次贝塞尔」算法对手写采样点做平滑，笔迹自然圆滑。
- **调色板与线宽**：6 色调色板 + 1–40px 可调线宽。
- **橡皮擦 / 精确擦除**：橡皮擦以背景色覆盖方式擦除（像素橡皮），并按「绘制顺序」层叠——只遮住它之前的笔迹，不会压住之后新画的笔迹。此外，**右键点击任意笔迹可整条删除**（对象级擦除），误删可用 Ctrl+Shift+Z / Ctrl+Y 恢复，非常适合课堂即时纠错。
- **撤销 / 重做 / 清空**：完整的笔迹编辑栈，工具栏按钮随场景状态自动启用/禁用。
- **保存 / 载入**：白板以 JSON 格式序列化，便于分发与二次处理。
- **画笔状态可视化**：当前激活颜色高亮，橡皮擦激活时颜色按钮自动降亮。
- **键盘快捷键**：为常用操作提供快捷键，书写过程中无需切换鼠标即可撤销/重做/清空/切换橡皮擦（见下方列表）。

## 键盘快捷键

| 快捷键 | 功能 |
| --- | --- |
| `Ctrl+Z` | 撤销 |
| `Ctrl+Shift+Z` / `Ctrl+Y` | 重做 |
| `Ctrl+L` | 清空白板 |
| `E` | 切换橡皮擦 |
| `Ctrl+S` | 保存白板（JSON） |
| `Ctrl+O` | 载入白板（JSON） |
| 右键点击笔迹 | 删除该条笔迹（可重做恢复） |

快捷键仅使用 Qt 6.2.4 已具备的 `QShortcut` / `QKeySequence` 公共 API，
挂载于主窗口（WindowShortcut 上下文），不影响工具栏按钮与鼠标书写流程。

## 测试

无界面（offscreen）冒烟与单元测试：

```bash
QT_QPA_PLATFORM=offscreen python3.11 tests/test_smoke.py
```

## 项目结构

```
main.py                  启动入口
smartboard/
  app.py                 应用入口（QApplication + MainWindow）
  main_window.py         主窗口与工具栏（状态联动）
  view.py                白板视图（鼠标交互 + 实时预览）
  whiteboard.py          核心：Stroke 数据模型、WhiteboardScene、路径平滑
tests/test_smoke.py      headless 测试套件
```

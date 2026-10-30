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
- **笔迹自动抽稀**：提交笔迹时用 Ramer–Douglas–Peucker 算法按 1.5px 容差抽掉冗余近共线采样点，白板文件体积与载入/渲染开销显著下降（典型连笔可减少 6–7× 点数、约 85% 体积），视觉几乎无差别。
- **调色板与线宽**：6 色调色板 + 1–40px 可调线宽。
- **橡皮擦 / 精确擦除**：橡皮擦以背景色覆盖方式擦除（像素橡皮），并按「绘制顺序」层叠——只遮住它之前的笔迹，不会压住之后新画的笔迹。此外，**右键点击任意笔迹可整条删除**（对象级擦除），误删可用 Ctrl+Shift+Z / Ctrl+Y 恢复，非常适合课堂即时纠错。
- **撤销 / 重做 / 清空**：完整的笔迹编辑栈，工具栏按钮随场景状态自动启用/禁用。
- **保存 / 载入 / 导出图片**：白板以 JSON 格式序列化，便于分发与二次处理；并可将当前白板**导出为 PNG 图片**（工具栏「导出图片」按钮），仅截取笔迹内容区域（含留白），所见即所得，方便课堂分享与留存。
- **画笔状态可视化**：当前激活颜色高亮，橡皮擦激活时颜色按钮自动降亮。
- **AI 教学联动**：工具栏「AI 答疑」可将当前白板内容作为上下文向 AI 提问；默认本地规则答疑（无需联网即可演示），可经环境变量 `SMARTBOARD_AI_ENDPOINT` / `SMARTBOARD_AI_KEY` 接入远程大模型（预留钩子，不写死密钥，不改动界面代码）。
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
QT_QPA_PLATFORM=offscreen python3 tests/test_smoke.py
QT_QPA_PLATFORM=offscreen python3 tests/test_ai_link.py
```

## 项目结构

```
main.py                  启动入口
smartboard/
  app.py                 应用入口（QApplication + MainWindow）
  main_window.py         主窗口与工具栏（状态联动 + AI 答疑入口）
  view.py                白板视图（鼠标交互 + 实时预览 + 图片导出）
  whiteboard.py          核心：Stroke 数据模型、WhiteboardScene、路径平滑
  ai_link.py             AI 教学联动（可插拔：本地答疑 / 远程后端钩子）
tests/test_smoke.py      headless 测试套件（37 项）
tests/test_ai_link.py    AI 联动单元测试（7 项）
```

"""智学白板 SmartBoard 启动入口。"""

import sys

from PySide6.QtWidgets import QApplication

from smartboard import MainWindow


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    # 崩溃恢复：若存在上次未保存的草稿，先询问是否恢复再展示主窗口。
    # 无草稿时不弹窗；该调用仅在真实启动流程，headless 测试直接构造 MainWindow
    # 不会走到这里，因此不阻塞任何测试。
    window._prompt_restore_draft()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

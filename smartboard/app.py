"""智学白板 SmartBoard 启动入口。"""

import sys

from PySide6.QtWidgets import QApplication

from smartboard import MainWindow


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

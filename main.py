#!/usr/bin/env python3
"""智学白板 SmartBoard 启动脚本。"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from smartboard.app import main

if __name__ == "__main__":
    main()

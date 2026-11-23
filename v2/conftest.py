"""pytest 路径引导：v2 目录（research 包）与仓库根目录（config / data_agent）。"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

for path in (str(HERE), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

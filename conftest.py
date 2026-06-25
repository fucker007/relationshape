"""把仓库根目录钉进 sys.path，让 `pytest`（入口脚本）和 `python -m pytest` 都能直接
import 顶层包（relationshape / growth），无需先 `pip install -e .`。

为什么需要它：入口脚本 `pytest` 不会把当前目录加入 sys.path（只有 `python -m pytest`
会）；而 tests/ 不是包（无 __init__.py），prepend 模式下 pytest 只把 tests/ 自己加进
路径——于是顶层包就 import 不到。这里在收集前把仓库根插到 sys.path 头部，一劳永逸。
"""

import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

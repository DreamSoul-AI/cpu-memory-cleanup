"""按需选择已经实现的操作系统接入，选择本身不采样。"""

import sys
from ..contracts import Capabilities


def select_backend(operating_system=None):
    name = sys.platform if operating_system is None else operating_system
    if name != "win32":
        return None, Capabilities(str(name), reason="当前操作系统尚无实时接入，离线回看仍可使用")
    from .platforms.windows import WindowsBackend
    return WindowsBackend(), Capabilities(name, reading="available", mechanisms=("working-set", "standby"),
                                         reason="接入已提供，实际指标、权限和维护支持在请求时核对")

"""可直接调用的包入口；导入和创建应用均不采样或打开界面。"""

from .application.core import Application
from .contracts import ActionRequest, Capabilities, RunReport, Sample, ProcessTarget
from .system.operating_system import select_backend
from .system.records import RecordStore
from .system.runtime import Runtime

API_VERSION = 1

__all__ = ["API_VERSION", "create_application", "ActionRequest", "Capabilities", "RunReport", "Sample", "ProcessTarget"]


def create_application(*, offline=False, reader=None, maintainer=None,
                       capabilities=None, records=None, runtime=None):
    """创建独立应用；offline 仅装配记录回看，注入能力时不选择真实平台。"""
    if type(offline) is not bool:
        raise ValueError("offline 必须是布尔值")
    if offline and (reader is not None or maintainer is not None):
        raise ValueError("离线应用不接入实时读取或维护")
    if offline:
        capabilities = Capabilities("offline", reason="离线应用只回看已有记录")
    elif reader is None and maintainer is None and capabilities is None:
        backend, capabilities = select_backend()
        reader = maintainer = backend
    return Application(reader, maintainer, capabilities=capabilities,
                       records=RecordStore() if records is None else records,
                       runtime=Runtime() if runtime is None else runtime)

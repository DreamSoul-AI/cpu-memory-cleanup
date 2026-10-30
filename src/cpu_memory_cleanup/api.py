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
    app = Application(reader, maintainer, capabilities=capabilities,
                      records=RecordStore() if records is None else records,
                      runtime=Runtime() if runtime is None else runtime)
    def preferences_factory(path):
        from .system.preferences import Preferences
        from .application.settings import validate_preferences
        return Preferences(path, validator=validate_preferences)
    app.preferences_factory = preferences_factory
    if app.capabilities.operating_system == "win32":
        def tray_factory(callback):
            from .system.platforms.windows_tray import WindowsTray
            return WindowsTray(callback)
        app.tray_factory = tray_factory
    return app

# 规则和范围辅助为无副作用的公开增量，原API_VERSION保持1。
from .application.targets import executable_groups, select_rows, targets_from_rows
from .application.policy import Policy, Rule
__all__ += ["executable_groups", "select_rows", "targets_from_rows", "Policy", "Rule"]


def create_web_server(*, app=None, output_dir=None, port=8767, preview=False):
    """正式网页装配：默认真实后端，构造不读取指标、不维护、不打开浏览器。"""
    from pathlib import Path
    from .interaction.web import LocalWebServer
    application = create_application() if app is None else app
    directory = Path.home() / ".cpu-memory-cleanup" / "runs" if output_dir is None else Path(output_dir)
    preferences = application.preferences_factory(directory.parent / "preferences.json")
    return LocalWebServer(application, directory, port=port, preview=preview, preferences=preferences)


__all__ += ["create_web_server"]

"""离线测试共享装配，运行信息为合成数据，不读取主机指标。"""

import logging
from cpu_memory_cleanup.application.core import Application
from cpu_memory_cleanup.contracts import Capabilities
from cpu_memory_cleanup.system.records import RecordStore


class FakeRuntime:
    def now(self):
        return "2026-10-22T00:00:00+00:00"

    def information(self):
        return {"build_id": "synthetic-build", "environment_id": "synthetic-environment",
                "environment": {"system": "fake", "version": "test", "python": "test"}}

    def diagnostic(self, event):
        logging.getLogger("cpu_memory_cleanup").warning(event["event"])


def create_application(*args, **kwargs):
    kwargs.setdefault("records", RecordStore())
    kwargs.setdefault("runtime", FakeRuntime())
    kwargs.setdefault("capabilities", Capabilities("fake", "available", ("working-set", "standby")))
    return Application(*args, **kwargs)

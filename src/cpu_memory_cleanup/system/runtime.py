"""按请求取得运行信息并输出诊断，不拥有业务状态。"""

from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import platform
import sys

from ..contracts import PROGRAM_VERSION

LOGGER = logging.getLogger("cpu_memory_cleanup")


class Runtime:
    def now(self):
        return datetime.now(timezone.utc).isoformat()

    def information(self):
        root = Path(__file__).resolve().parents[1]
        digest = hashlib.sha256()
        for path in sorted(root.rglob("*.py"), key=lambda p: p.relative_to(root).as_posix()):
            digest.update(path.relative_to(root).as_posix().encode("utf-8"))
            digest.update(path.read_bytes())
        return {"build_id": digest.hexdigest(),
                "environment_id": f"{platform.system()}-{platform.version()}-python{platform.python_version()}",
                "environment": {"system": platform.system(), "version": platform.version(),
                                "python": platform.python_version()}}

    def diagnostic(self, event):
        record = {**event, "timestamp": self.now(), "logger": LOGGER.name,
                  "app_name": "CPU 内存清理", "app_version": PROGRAM_VERSION,
                  "process_name": Path(sys.executable).name, "pid": os.getpid()}
        LOGGER.warning(json.dumps(record, ensure_ascii=False, allow_nan=False))

"""有界JSON偏好原子替换，不存储规则武装状态。"""

import json
import os
from pathlib import Path
import tempfile


MAX_BYTES = 1024 * 1024




class Preferences:
    def __init__(self, path, *, validator):
        self.path = Path(path)
        self.validate = validator

    def load(self):
        if not self.path.exists():
            return self.validate({"schema_version": 1})
        if self.path.stat().st_size > MAX_BYTES:
            raise ValueError("偏好文件过大")
        return self.validate(json.loads(self.path.read_text(encoding="utf-8")))

    def save(self, value):
        content = json.dumps(self.validate(value), ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8")
        if len(content) > MAX_BYTES:
            raise ValueError("偏好文件过大")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.path.parent, prefix=".preferences-", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

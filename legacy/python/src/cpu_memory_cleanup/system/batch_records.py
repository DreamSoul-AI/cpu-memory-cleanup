"""独立批次索引的物理读写；每项确认后立即落盘。"""

import json
import os
from pathlib import Path

from ..contracts import MAX_FILE_BYTES, MAX_LINE_BYTES
from .records import _pairs


class BatchJournal:
    def __init__(self, base, run_id):
        self.directory = Path(base) / run_id
        self.directory.mkdir(parents=True, exist_ok=False)
        self.stream = (self.directory / "batch.jsonl").open("x", encoding="utf-8", newline="\n")

    def append(self, record):
        line = json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n"
        size = len(line.encode("utf-8"))
        if size > MAX_LINE_BYTES or self.stream.tell() + size > MAX_FILE_BYTES:
            raise ValueError("批次索引超过大小上限")
        self.stream.write(line)
        self.stream.flush()
        os.fsync(self.stream.fileno())

    def close(self):
        self.stream.close()


def read_index(directory):
    path = Path(directory) / "batch.jsonl"
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("批次索引超过大小上限")
    records = []
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if len(line.encode("utf-8")) > MAX_LINE_BYTES:
                raise ValueError("批次行超过上限")
            records.append(json.loads(line, object_pairs_hook=_pairs,
                           parse_constant=lambda value: (_ for _ in ()).throw(ValueError("非有限JSON"))))
    return records

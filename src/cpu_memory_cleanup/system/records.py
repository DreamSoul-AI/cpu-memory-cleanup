"""记录的物理访问。业务版本、运行语义和动作判定由功能层解释。"""

import json
import os
from pathlib import Path

from ..contracts import MAX_FILE_BYTES, MAX_LINE_BYTES, finite_number


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


class Journal:
    def __init__(self, run_id, options):
        self.run_id = run_id
        self.streams = {}
        self.directory = None
        self.sealed = False
        self.committed = False
        try:
            if options.get("jsonl_path") is not None:
                path = Path(options["jsonl_path"])
                self.directory = path.parent
            else:
                base = Path(options["output_dir"])
                base.mkdir(parents=True, exist_ok=True)
                self.directory = base / run_id
                self.directory.mkdir()
                path = self.directory / "samples.jsonl"
            self.streams["samples"] = path.open("x", encoding="utf-8", newline="\n")
            self.sample_path = path
            self.output_dir = str(self.directory)
        except Exception:
            self.close()
            raise

    def append(self, kind, value):
        if self.sealed or kind not in ("samples", "operation"):
            raise ValueError("记录已关闭或通道无效")
        if kind not in self.streams:
            self.streams[kind] = (self.directory / "operation.jsonl").open("x", encoding="utf-8", newline="\n")
        stream = self.streams[kind]
        record = {"run_id": self.run_id, **value}
        encoded = _json(record) + "\n"
        if len(encoded.encode("utf-8")) > MAX_LINE_BYTES or stream.tell() + len(encoded.encode("utf-8")) > MAX_FILE_BYTES:
            raise ValueError("运行记录超出大小上限")
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())

    def close(self):
        self.sealed = True
        errors = []
        for kind, stream in list(self.streams.items()):
            try:
                stream.close()
            except Exception as error:
                errors.append({"type": type(error).__name__, "message": "记录流关闭失败", "channel": kind})
            else:
                del self.streams[kind]
        return {"closed": not self.streams, "errors": errors}

    def commit_end(self, terminal):
        if not self.sealed or self.streams or self.committed:
            raise RuntimeError("终态只能在记录确认关闭后提交一次")
        # 临时文件先关闭，再原子替换，避免关闭失败后磁盘留下“整体成功”。
        content = self.sample_path.read_bytes()
        final = (_json({"run_id": self.run_id, **terminal}) + "\n").encode("utf-8")
        if len(content) + len(final) > MAX_FILE_BYTES:
            raise ValueError("结束记录超出大小上限")
        temporary = self.sample_path.with_name(f".{self.run_id}.pending")
        stream = temporary.open("xb")
        self.streams["terminal"] = stream
        stream.write(content + final)
        stream.flush()
        os.fsync(stream.fileno())
        stream.close()
        del self.streams["terminal"]
        os.replace(temporary, self.sample_path)
        self.committed = True


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"重复 JSON 字段：{key}")
        result[key] = value
    return result


def _number(text):
    value = float(text)
    finite_number(value, "JSON 数值", minimum=-float("inf"))
    return value


class RunReader:
    def __init__(self, path):
        path = Path(path)
        self.is_directory = path.is_dir()
        self.sample_path = path / "samples.jsonl" if self.is_directory else path
        self.streams = []
        self.closed = False

    @property
    def has_actions(self):
        return (self.sample_path.parent / "operation.jsonl").exists()

    def read(self, kind="samples"):
        if self.closed:
            raise RuntimeError("记录读取会话已关闭")
        if kind not in ("samples", "operation"):
            raise ValueError("记录通道无效")
        path = self.sample_path if kind == "samples" else self.sample_path.parent / "operation.jsonl"
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("文件超出大小上限")
        stream = path.open(encoding="utf-8-sig")
        self.streams.append(stream)
        for number, line in enumerate(stream, 1):
            if len(line.encode("utf-8")) > MAX_LINE_BYTES:
                raise ValueError(f"第 {number} 行超出大小上限")
            if not line.strip():
                continue
            try:
                record = json.loads(line, object_pairs_hook=_pairs, parse_float=_number,
                                    parse_constant=lambda text: (_ for _ in ()).throw(ValueError("非有限 JSON 数值")))
                if not isinstance(record, dict):
                    raise ValueError("记录必须是对象")
            except (ValueError, RecursionError) as error:
                raise ValueError(f"第 {number} 行：记录无效") from error
            yield record

    def close(self):
        errors = []
        for stream in list(self.streams):
            try:
                stream.close()
            except Exception as error:
                errors.append({"type": type(error).__name__, "message": "记录流关闭失败"})
            else:
                self.streams.remove(stream)
        self.closed = not self.streams
        return {"closed": self.closed, "errors": errors}


class RecordStore:
    def open_write(self, run_id, options):
        return Journal(run_id, options)

    def open_read(self, path):
        return RunReader(path)

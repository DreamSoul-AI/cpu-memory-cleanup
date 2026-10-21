import tempfile
from pathlib import Path
import unittest

from cpu_memory_cleanup.system.records import RecordStore


class RecordTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = RecordStore()

    def test_writer_does_not_overwrite_an_existing_sample_file(self):
        path = Path(self.temp.name) / "已有样本.jsonl"
        path.write_text("original", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            self.store.open_write("run", {"jsonl_path": path})
        self.assertEqual(path.read_text(encoding="utf-8"), "original")

    def test_readonly_records_have_no_fabricated_action_file(self):
        writer = self.store.open_write("run", {"output_dir": self.temp.name})
        writer.append("samples", {"record_type": "header"})
        self.assertTrue(writer.close()["closed"])
        writer.commit_end({"record_type": "end", "completion": "success"})
        reader = self.store.open_read(writer.output_dir)
        self.addCleanup(reader.close)
        self.assertFalse(reader.has_actions)
        self.assertEqual([r["record_type"] for r in reader.read()], ["header", "end"])

    def test_invalid_file_keeps_its_line_number_and_can_close(self):
        path = Path(self.temp.name) / "bad.jsonl"
        path.write_text('{}\n{"value":NaN}\n', encoding="utf-8")
        reader = self.store.open_read(path)
        try:
            with self.assertRaisesRegex(ValueError, "第 2 行"):
                list(reader.read())
        finally:
            facts = reader.close()
        self.assertEqual(facts, {"closed": True, "errors": []})

    def test_duplicate_fields_are_rejected(self):
        path = Path(self.temp.name) / "bad.jsonl"
        path.write_text('{"x":1,"x":2}', encoding="utf-8")
        reader = self.store.open_read(path)
        self.addCleanup(reader.close)
        with self.assertRaises(ValueError):
            list(reader.read())

    def test_closed_writer_cannot_append_or_commit_twice(self):
        writer = self.store.open_write("run", {"output_dir": self.temp.name})
        writer.append("samples", {"record_type": "header"})
        self.assertTrue(writer.close()["closed"])
        with self.assertRaises(ValueError):
            writer.append("operation", {"record_type": "intent"})
        writer.commit_end({"record_type": "end"})
        with self.assertRaises(RuntimeError):
            writer.commit_end({"record_type": "end"})

    def test_closed_reader_cannot_reopen_a_file(self):
        path = Path(self.temp.name) / "sample.jsonl"
        path.write_text("{}\n", encoding="utf-8")
        reader = self.store.open_read(path)
        self.assertTrue(reader.close()["closed"])
        with self.assertRaises(RuntimeError):
            list(reader.read())

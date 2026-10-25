"""无控制台桌面入口。双击时使用系统关联的窗口版 Python。"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def main(argv=None):
    try:
        from cpu_memory_cleanup.__main__ import main as start
        return start(["--gui", *(sys.argv[1:] if argv is None else argv)])
    except Exception as error:
        from tkinter import messagebox
        messagebox.showerror("内存清理启动失败", str(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

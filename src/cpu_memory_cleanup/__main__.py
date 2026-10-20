"""程序装配；只有显式选择桌面入口才加载 Tk。"""

from . import cli
from .core import Application
from .windows import WindowsBackend


def main(argv=None, *, app=None):
    args = cli.parse_args(argv)
    if app is None:
        backend = WindowsBackend()
        app = Application(reader=backend, maintainer=backend)
    if args.gui:
        from . import desktop
        return desktop.run(app, {"output_dir": args.output_dir})
    return cli.run(app, args)


if __name__ == "__main__":
    raise SystemExit(main())

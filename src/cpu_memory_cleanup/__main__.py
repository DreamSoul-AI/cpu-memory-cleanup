"""启动装配，离线回看独立于具体操作系统接入。"""

from .interaction import cli
from .application.core import Application
from .system.operating_system import select_backend
from .system.records import RecordStore
from .system.runtime import Runtime


def main(argv=None, *, app=None):
    args = cli.parse_args(argv)
    if app is None:
        offline = any((args.review, args.analyze, args.pressure, args.tree_summary, args.ab_compare))
        if offline:
            app = Application(records=RecordStore(), runtime=Runtime())
        else:
            backend, capabilities = select_backend()
            app = Application(reader=backend, maintainer=backend, capabilities=capabilities,
                              records=RecordStore(), runtime=Runtime())
    if args.gui:
        from .interaction import desktop
        return desktop.run(app, {"output_dir": args.output_dir})
    return cli.run(app, args)


if __name__ == "__main__":
    raise SystemExit(main())

"""选择交互入口，统一复用包公共装配。"""

import json
from .api import create_application
from .interaction import cli


def main(argv=None, *, app=None):
    args = cli.parse_args(argv)
    if args.tool_info:
        print(json.dumps(cli.tool_info(), ensure_ascii=True, allow_nan=False, indent=2))
        return 0
    if app is None:
        offline = any((args.review, args.analyze, args.pressure, args.tree_summary, args.ab_compare,
                       args.tool == "memory_result"))
        app = create_application(offline=offline)
    if args.gui:
        from .interaction import desktop
        return desktop.run(app, {"output_dir": args.output_dir})
    return cli.run(app, args)


if __name__ == "__main__":
    raise SystemExit(main())

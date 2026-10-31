"""选择交互入口，统一复用包公共装配。"""

import json
import sys
from .api import create_application
from .interaction import cli


def main(argv=None, *, app=None):
    args = cli.parse_args(argv)
    if args.tool_info:
        print(json.dumps(cli.tool_info(), ensure_ascii=True, allow_nan=False, indent=2))
        return 0
    if args.web:
        from .api import create_web_server
        from .interaction.web import serve
        port = 0 if args.port is None else args.port
        try:
            server = create_web_server(app=app, output_dir=args.output_dir, port=port)
        except OSError as error:
            code = getattr(error, "winerror", None) or error.errno
            print(f"本地网页启动失败（系统错误 {code}）。请检查端口是否被占用或系统保留、"
                  "记录及偏好目录是否可访问；可用 --port 0 让系统分配端口。", file=sys.stderr)
            return 1
        return serve(server)
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

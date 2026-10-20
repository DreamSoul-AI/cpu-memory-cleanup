"""命令行参数与结果呈现；业务状态和执行事实由 Application 提供。"""

import argparse
import json
import math
from pathlib import Path
import sys

from .contracts import ActionRequest, MECHANISMS


def _count(text):
    value = int(text)
    if not 1 <= value <= 1000:
        raise argparse.ArgumentTypeError("必须为 1..1000 的整数")
    return value


def _interval(text):
    value = float(text)
    if not math.isfinite(value) or not 0.1 <= value <= 3600:
        raise argparse.ArgumentTypeError("间隔必须为 0.1..3600 秒的有限数")
    return value


def _top(text):
    value = int(text)
    if not 1 <= value <= 100:
        raise argparse.ArgumentTypeError("进程数量必须为 1..100 的整数")
    return value


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="CPU 内存清理：默认只读，维护须明确指定单一机制。")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--gui", action="store_true", help="打开桌面界面；打开时只读")
    mode.add_argument("--sample", action="store_true", help="有界只读采样")
    mode.add_argument("--release", action="store_true", help="明确确认执行一次单项维护")
    mode.add_argument("--review", type=Path, help="离线回看运行目录或 JSONL")
    mode.add_argument("--analyze", type=Path, help="--review 的兼容别名")
    mode.add_argument("--pressure", type=Path, help="--review 的兼容别名")
    mode.add_argument("--tree-summary", type=Path, help="--review 的兼容别名")
    mode.add_argument("--ab-compare", type=Path, nargs=2, metavar=("A", "B"), help="离线比较两个运行")
    parser.add_argument("--comparison", type=Path, help="--review 的对照运行")
    parser.add_argument("--json", action="store_true", help="输出完整 JSON 结果")
    parser.add_argument("--output-dir", type=Path, help="显式运行记录目录；维护时必需")
    parser.add_argument("--release-what", choices=(*MECHANISMS, "both"), help="仅支持 working-set 或 standby")
    parser.add_argument("--count", type=_count)
    parser.add_argument("--interval", type=_interval)
    parser.add_argument("--jsonl", type=Path, help="保存采样 JSONL，不重建历史 logs")
    parser.add_argument("--with-processes", action="store_true")
    parser.add_argument("--top", type=_top)
    for flag in ("--ab-protocol", "--ab-pattern", "--ab-interval", "--ab-count", "--ab-what",
                 "--pressure-follow", "--polls", "--poll-interval", "--free-below-gib",
                 "--commit-above-pct", "--sustain"):
        parser.add_argument(flag, help=argparse.SUPPRESS)
    parser.add_argument("--ab-null", nargs=2, action="append", help=argparse.SUPPRESS)
    parser.add_argument("--ab-live", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--release-preflight", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    unsupported = ("ab_protocol", "ab_pattern", "ab_interval", "ab_count", "ab_what", "ab_null", "ab_live",
                   "pressure_follow", "polls", "poll_interval", "free_below_gib", "commit_above_pct", "sustain",
                   "release_preflight")
    if any(getattr(args, name) is not None and getattr(args, name) is not False for name in unsupported):
        parser.error("旧组合实验、跟随、独立预检与自定义阈值入口已停用；请使用 --review 或 --sample")
    if args.release_what == "both":
        parser.error("both 已停用；一次维护只允许明确的一种机制")
    if args.release:
        if args.release_what is None or args.output_dir is None:
            parser.error("--release 必须同时指定 --release-what 和 --output-dir")
    elif args.release_what is not None:
        parser.error("--release-what 需要 --release")
    readonly_path = args.review or args.analyze or args.pressure or args.tree_summary
    if args.comparison is not None and args.review is None:
        parser.error("--comparison 需要 --review")
    sampling_flags = (args.count is not None or args.interval is not None or args.jsonl is not None
                      or args.with_processes or args.top is not None)
    if sampling_flags and not args.sample:
        parser.error("--count、--interval、--jsonl、--with-processes、--top 需要 --sample")
    if args.sample and (args.count is None or args.interval is None):
        parser.error("--sample 必须指定 --count 和 --interval")
    if args.sample and args.jsonl is None and args.output_dir is None and not args.json:
        parser.error("--sample 必须指定 --jsonl、--output-dir 或 --json")
    if args.jsonl is not None and args.output_dir is not None:
        parser.error("--jsonl 与 --output-dir 不能同时指定")
    if args.output_dir is not None and (readonly_path is not None or args.ab_compare is not None):
        parser.error("离线回看不写入 --output-dir")
    if args.gui and args.json:
        parser.error("--gui 不支持 --json")
    return args


def format_report(report):
    record = report.to_dict()
    lines = ["CPU 内存清理", f"模式：{record['mode']}，完成状态：{record['completion']}",
             f"运行：{record['run_id']}"]
    action = record.get("action") or {}
    lines.append(f"原生维护调用：{action.get('attempted', False)}；结果：{action.get('call_outcome', 'unknown')}")
    for field in ("analysis", "errors"):
        if record.get(field):
            lines.append(json.dumps(record[field], ensure_ascii=False, allow_nan=False, indent=2))
    return "\n".join(lines)


def run(app, args):
    try:
        path = args.review or args.analyze or args.pressure or args.tree_summary
        if args.ab_compare is not None:
            report = app.review(*args.ab_compare)
        elif path is not None:
            report = app.review(path, comparison=args.comparison)
        elif args.release:
            request = ActionRequest(args.release_what, confirmed=True, confirmation_source="cli:--release")
            report = app.maintain(request, args.output_dir)
        else:
            options = {"count": args.count or 1, "interval": args.interval if args.interval is not None else 1,
                       "with_processes": args.with_processes, "top": args.top or 10}
            if args.output_dir is not None:
                options["output_dir"] = args.output_dir
            if args.jsonl is not None:
                options["jsonl_path"] = args.jsonl
            report = app.inspect(options)
        print(json.dumps(report.to_dict(), ensure_ascii=False, allow_nan=False, indent=2) if args.json else format_report(report))
        return 0 if report.completion == "success" else 1
    except (OSError, ValueError) as error:
        message = {"completion": "failed", "error": str(error)}
        print(json.dumps(message, ensure_ascii=False) if args.json else str(error), file=sys.stderr)
        return 1

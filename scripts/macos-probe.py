"""macOS-only native readonly evidence. Never invokes maintenance tools."""
import argparse
import json
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    if platform.system() != "Darwin":
        parser.error("Run this probe on an actual macOS host.")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    commands = {
        "physical-total": ["/usr/sbin/sysctl", "-n", "hw.memsize"],
        "page-size": ["/usr/sbin/sysctl", "-n", "hw.pagesize"],
        "vm-stat": ["/usr/bin/vm_stat"],
        "swap": ["/usr/sbin/sysctl", "vm.swapusage"],
        "boot-time": ["/usr/sbin/sysctl", "kern.boottime"],
        "targets": ["/bin/ps", "-axo", "pid=,lstart=,rss=,comm="],
        "catalog": [str(args.binary.resolve()), "--tool-info"],
        "product-status": [str(args.binary.resolve()), "--tool", "memory_status"],
    }
    receipt = {"started_at": datetime.now(timezone.utc).isoformat(), "platform": platform.platform(), "architecture": platform.machine(), "maintenance_calls": 0, "commands": {}}
    for name, argv in commands.items():
        result = subprocess.run(argv, capture_output=True, text=True, timeout=30)
        (args.output_dir / (name + ".stdout.txt")).write_text(result.stdout, encoding="utf-8")
        (args.output_dir / (name + ".stderr.txt")).write_text(result.stderr, encoding="utf-8")
        receipt["commands"][name] = {"argv": argv, "exit_code": result.returncode}
        (args.output_dir / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    # OS probes are required; product unsupported remains an explicit result,
    # not fabricated capacity or an assertion that macOS integration is complete.
    required = [name for name in commands if name != "product-status"]
    if any(receipt["commands"][name]["exit_code"] != 0 for name in required):
        raise SystemExit("Required native readonly probe failed; see saved evidence.")
    print(json.dumps(receipt, ensure_ascii=False))


if __name__ == "__main__":
    main()

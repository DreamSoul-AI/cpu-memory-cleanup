"""macOS-only native readonly evidence. Never invokes maintenance tools."""
import argparse
import json
import platform
import os
import re
import tempfile
import urllib.request
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--launcher", type=Path)
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
        "product-targets": [str(args.binary.resolve()), "--tool", "memory_targets"],
        "product-targets-again": [str(args.binary.resolve()), "--tool", "memory_targets"],
    }
    receipt = {"started_at": datetime.now(timezone.utc).isoformat(), "platform": platform.platform(), "architecture": platform.machine(), "maintenance_calls": 0, "commands": {}}
    for name, argv in commands.items():
        result = subprocess.run(argv, capture_output=True, text=True, timeout=30)
        (args.output_dir / (name + ".stdout.txt")).write_text(result.stdout, encoding="utf-8")
        (args.output_dir / (name + ".stderr.txt")).write_text(result.stderr, encoding="utf-8")
        receipt["commands"][name] = {"argv": argv, "exit_code": result.returncode}
        (args.output_dir / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    if any(value["exit_code"] != 0 for value in receipt["commands"].values()):
        raise SystemExit("Native/product readonly probe failed; see saved evidence.")
    def result(name):
        return json.loads((args.output_dir / (name + ".stdout.txt")).read_text(encoding="utf-8"))
    catalog = result("catalog")
    assert catalog["platform_capabilities"]["reading"] is True
    assert catalog["platform_capabilities"]["maintenance"] is False
    status = result("product-status")
    physical = status["samples"][0]["metrics"]["physical"]
    assert status["completion"] == "success"
    assert physical["total_bytes"] == int((args.output_dir / "physical-total.stdout.txt").read_text())
    assert 0 <= physical["available_bytes"] <= physical["total_bytes"]
    assert physical["available_kind"] == "free_and_inactive_estimate"
    assert status["samples"][0]["metrics"]["commit"]["used_bytes"] is None
    targets = result("product-targets")["samples"][0]["processes"]
    own = next(p for p in targets if p["pid"] == os.getpid())
    repeated = next(p for p in result("product-targets-again")["samples"][0]["processes"] if p["pid"] == os.getpid())
    assert own["created_at"] == repeated["created_at"]
    assert re.fullmatch(r"[0-9]+:[0-9]{6}", own["created_at"])
    assert own["working_set_bytes"] > 0
    receipt["product"] = {"build_id":catalog["build_id"], "own_identity":own, "readonly_success":True}
    command = ["/bin/sh", str(args.launcher.resolve())] if args.launcher else [str(args.binary.resolve()), "--web"]
    process = subprocess.Popen([*command, "--port", "0"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    token = None
    url = None
    stopped = False
    def http(path, body=None):
        payload = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(url + path, data=payload, headers={"X-Session-Token":token or "", "Content-Type":"application/json"})
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.load(response)
    try:
        url = process.stdout.readline().strip()
        assert re.fullmatch(r"http://127\.0\.0\.1:[0-9]+/", url), url
        session = http("api/session")
        token = session.pop("token")
        assert session["build_id"] == catalog["build_id"]
        assert session["reading"] == "available" and session["capabilities"]["mechanisms"] == []
        reading = http("api/display", {})
        assert reading["completion"] == "success" and reading["metadata"]["build_id"] == catalog["build_id"]
        (args.output_dir / "http-session.json").write_text(json.dumps(session, indent=2), encoding="utf-8")
        (args.output_dir / "http-reading.json").write_text(json.dumps(reading, indent=2), encoding="utf-8")
        chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        if not chrome.exists():
            raise RuntimeError("Native headless browser unavailable; UI verification not completed")
        with tempfile.TemporaryDirectory(prefix="memory-mac-browser-") as profile:
            common = [str(chrome), "--headless", "--disable-gpu", "--no-first-run", "--no-default-browser-check", "--user-data-dir=" + profile, "--window-size=620,840", "--virtual-time-budget=4000"]
            browser = subprocess.run([*common, "--dump-dom", url], capture_output=True, text=True, timeout=40)
            (args.output_dir / "browser.html").write_text(browser.stdout, encoding="utf-8")
            (args.output_dir / "browser.stderr.txt").write_text(browser.stderr, encoding="utf-8")
            assert browser.returncode == 0
            texts = {}
            for name in ["actual-percent", "capacity", "commit", "gauge-label"]:
                match = re.search(r'id="' + name + r'"[^>]*>([^<]*)<', browser.stdout)
                assert match, name
                texts[name] = match.group(1)
            assert texts["actual-percent"].endswith("%"), texts
            assert "GiB" in texts["capacity"] and "估算" in texts["gauge-label"]
            assert texts["commit"] == "当前平台未提供提交统计"
            clean = re.search(r'<button[^>]*id="global-clean"[^>]*>', browser.stdout)
            assert clean and "disabled" in clean.group(0)
            screenshot = subprocess.run([*common, "--screenshot=" + str((args.output_dir / "browser.png").resolve()), url], capture_output=True, text=True, timeout=40)
            assert screenshot.returncode == 0 and (args.output_dir / "browser.png").exists()
            receipt["browser"] = {"exit_code":browser.returncode, "texts":texts, "maintenance_disabled":True, "version":subprocess.check_output([str(chrome), "--version"], text=True).strip()}
        assert http("api/state")["rule"]["armed"] is False
        http("api/shutdown", {"confirmed":True})
        process.wait(timeout=15)
        assert process.returncode == 0
        stopped = True
        receipt["service"] = {"session":session, "exit_code":process.returncode, "stopped":True, "launcher":str(args.launcher) if args.launcher else None}
    finally:
        if process.poll() is None:
            try:
                if token:
                    http("api/shutdown", {"confirmed":True})
                process.wait(timeout=15)
            except Exception:
                process.terminate()  # Only the exact readonly child created above.
                process.wait(timeout=10)
        receipt["service_stopped"] = stopped
        (args.output_dir / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False))



if __name__ == "__main__":
    main()

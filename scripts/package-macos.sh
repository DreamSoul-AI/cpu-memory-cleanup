#!/bin/sh
# Native macOS readonly portable candidate. No active reclaim.
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
[ "$(uname -s)" = Darwin ] || { echo "Build this candidate on macOS." >&2; exit 2; }
output=${1:-dist/macos}
[ ! -e "$output" ] || { echo "Output exists; choose a new directory." >&2; exit 2; }
cargo build --release --locked
target=${CARGO_TARGET_DIR:-target}
binary="$target/release/cpu-memory-cleanup"
version=$(sed -n 's/^version = "\([^"]*\)"/\1/p' Cargo.toml | head -n 1)
name="cpu-memory-cleanup-$version-macos-$(uname -m)"
mkdir -p "$output/$name"
"$binary" --tool-info > "$output/$name/build.json"
cp "$binary" LICENSE "$output/$name/"
cat > "$output/$name/run.command" <<'RUN'
#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "$root/cpu-memory-cleanup" --web --output-dir "$HOME/Library/Application Support/DreamSoul/CPU Memory Cleanup/runs" "$@"
RUN
chmod +x "$output/$name/run.command"
cat > "$output/$name/README.txt" <<'INFO'
macOS readonly candidate. No active memory reclaim.
Run ./run.command, then open the printed localhost URL in a browser.
Stop using the page's Stop service action; closing the tab does not stop it.
CLI: ./cpu-memory-cleanup --tool memory_status
Availability is a free+inactive page estimate. Commit statistics are unavailable.
No Rust or Node runtime is required. Unsigned and not notarized.
Gatekeeper, user download and ordinary desktop experience need separate verification.
INFO
tar -czf "$output/$name.tar.gz" -C "$output" "$name"
(cd "$output" && shasum -a 256 "$name.tar.gz" > SHA256SUMS.txt)
printf '%s\n' "$output/$name.tar.gz"

#!/bin/sh
# Native Linux read-only candidate; does not start a service or reclaim memory.
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
[ "$(uname -s)" = Linux ] || { echo "Build this candidate on Linux." >&2; exit 2; }
output=${1:-dist/linux}
[ ! -e "$output" ] || { echo "Output exists; choose a new directory." >&2; exit 2; }
cargo build --release --locked
target=${CARGO_TARGET_DIR:-target}
binary="$target/release/cpu-memory-cleanup"
version=$(sed -n 's/^version = "\([^"]*\)"/\1/p' Cargo.toml | head -n 1)
name="cpu-memory-cleanup-$version-linux-$(uname -m)"
mkdir -p "$output/$name"
"$binary" --tool-info > "$output/$name/build.json"
cp "$binary" LICENSE "$output/$name/"
cat > "$output/$name/run.sh" <<'RUN'
#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
state=${XDG_STATE_HOME:-"$HOME/.local/state"}
exec "$root/cpu-memory-cleanup" --web --output-dir "$state/cpu-memory-cleanup/runs" "$@"
RUN
chmod +x "$output/$name/run.sh"
cat > "$output/$name/README.txt" <<'INFO'
Linux read-only candidate. No active memory reclaim.
Run ./run.sh, then open the printed localhost URL in a browser.
Stop using the page's Stop service action; closing the tab does not stop it.
CLI: ./cpu-memory-cleanup --tool memory_status
Readings belong to the current Linux host/VM, not its Windows host.
No Rust or Node runtime is required. Build host libc compatibility still applies.
INFO
tar -czf "$output/$name.tar.gz" -C "$output" "$name"
(cd "$output" && sha256sum "$name.tar.gz" > SHA256SUMS.txt)
printf '%s\n' "$output/$name.tar.gz"

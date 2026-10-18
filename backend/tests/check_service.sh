#!/usr/bin/env bash
# Run from any directory: bash backend/tests/check_service.sh
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
TEMP="$(mktemp -d)"
trap 'rm -rf "$TEMP"' EXIT
cat > "$TEMP/systemctl" <<'SH'
#!/usr/bin/env bash
if [[ "$1" == show ]]; then
    echo loaded
else
    printf '%s\n' "$*" >> "$CALLS"
    exit "${RESULT:-0}"
fi
SH
chmod +x "$TEMP/systemctl"
export PATH="$TEMP:$PATH" CALLS="$TEMP/calls"
unset WORDS_SKIP_SYSTEMD
for action in start stop restart status; do
    bash "$ROOT/sop.sh" "$action"
done
printf '%s\n' 'start words.service' 'stop words.service' 'restart words.service' 'status words.service' > "$TEMP/expected"
cmp "$CALLS" "$TEMP/expected"
if RESULT=3 bash "$ROOT/sop.sh" status; then
    echo 'Expected systemctl exit code 3' >&2
    exit 1
else
    [[ $? == 3 ]]
fi
bash -n "$ROOT/setup_service.sh"
bash -n "$ROOT/sop.sh"
echo 'systemd delegation and exit status checks passed'

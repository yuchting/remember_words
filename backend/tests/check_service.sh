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
printf '%s\n' '--no-pager start words.service' '--no-pager stop words.service' '--no-pager restart words.service' '--no-pager status words.service' > "$TEMP/expected"
cmp "$CALLS" "$TEMP/expected"
if RESULT=3 bash "$ROOT/sop.sh" status; then
    echo 'Expected systemctl exit code 3' >&2
    exit 1
else
    [[ $? == 3 ]]
fi
bash "$ROOT/sop.sh" > "$TEMP/help"
grep -q 'service' "$TEMP/help"
[[ ! -e "$ROOT/setup_service.sh" ]]
if bash "$ROOT/sop.sh" service unexpected > "$TEMP/error" 2>&1; then
    echo 'Expected extra service argument to fail' >&2
    exit 1
fi
grep -q 'service 不支持额外参数' "$TEMP/error"
bash -n "$ROOT/sop.sh"
echo 'systemd delegation and exit status checks passed'

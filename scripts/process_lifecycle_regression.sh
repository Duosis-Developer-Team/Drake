#!/usr/bin/env bash
# Negative regression for scripts/lib/process_lifecycle.sh.
#
# Every case here is one that already happened or that the cleanup code
# would silently get wrong. The important one is GRANDCHILDREN: the leak
# this library was written for was a `pnpm` wrapper whose `next-server`
# child survived, held port 13100 for two days and re-parented to init.
# A test that only checks the direct child would have passed then too.
set -uo pipefail

cd "$(dirname "$0")/.."
LIB="$PWD/scripts/lib/process_lifecycle.sh"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

PASS=0
FAIL=0
ok() {
  if [ "$1" = "0" ]; then printf 'OK   %s\n' "$2"; PASS=$((PASS + 1))
  else printf 'FAIL %s\n' "$2"; FAIL=$((FAIL + 1)); fi
}
alive() { kill -0 "$1" 2>/dev/null; }

# A child that itself spawns a child, mirroring `pnpm` -> `next-server`.
# The inner pid is written out so the test can check the GRANDCHILD.
cat > "$WORK/parent.sh" <<'EOS'
#!/usr/bin/env bash
sleep 600 &
echo $! > "$1"
wait
EOS
chmod +x "$WORK/parent.sh"

wait_gone() {
  local pid="$1" n=0
  while [ $n -lt 20 ]; do alive "$pid" || return 0; sleep 0.5; n=$((n + 1)); done
  return 1
}

run_case() {
  # $1 = script body placed after the spawn, $2 = signal to send (or "")
  local body="$1" signal="${2:-}"
  cat > "$WORK/case.sh" <<EOS
#!/usr/bin/env bash
set -uo pipefail
. "$LIB"
lifecycle_install_traps
"$WORK/parent.sh" "$WORK/grandchild.pid" &
lifecycle_track \$! "parent"
# Give the grandchild time to exist before anything else happens.
for _ in \$(seq 1 20); do [ -s "$WORK/grandchild.pid" ] && break; sleep 0.2; done
$body
EOS
  chmod +x "$WORK/case.sh"
  rm -f "$WORK/grandchild.pid"
  "$WORK/case.sh" & CASE_PID=$!
  for _ in $(seq 1 40); do [ -s "$WORK/grandchild.pid" ] && break; sleep 0.2; done
  GRANDCHILD="$(cat "$WORK/grandchild.pid" 2>/dev/null || true)"
  if [ -n "$signal" ]; then
    sleep 0.5
    kill "-$signal" "$CASE_PID" 2>/dev/null || true
  fi
  wait "$CASE_PID" 2>/dev/null
  CASE_STATUS=$?
}

echo "### 1. the script FAILS -> children and grandchildren must still die"
run_case 'exit 1' ""
ok "$([ "$CASE_STATUS" = "1" ] && echo 0 || echo 1)" "failing script keeps its own non-zero exit status ($CASE_STATUS)"
ok "$(wait_gone "$GRANDCHILD" && echo 0 || echo 1)" "grandchild is gone after a FAILING run (pid $GRANDCHILD)"

echo "### 2. the script SUCCEEDS -> nothing is left behind"
run_case 'exit 0' ""
ok "$([ "$CASE_STATUS" = "0" ] && echo 0 || echo 1)" "passing script exits 0"
ok "$(wait_gone "$GRANDCHILD" && echo 0 || echo 1)" "grandchild is gone after a PASSING run (pid $GRANDCHILD)"

echo "### 3. SIGINT (Ctrl+C) -> cleanup path runs, conventional 130"
# Sent to the process GROUP from a fresh session, which is what a terminal
# actually does on Ctrl+C. Backgrounding the script with `&` instead would
# test nothing: bash starts async commands with SIGINT set to SIG_IGN, and
# POSIX forbids trapping a signal that was already ignored on entry to a
# non-interactive shell — the handler could never run, in the test OR in
# the library, and the test would be measuring its own harness.
cat > "$WORK/case_int.sh" <<EOS
#!/usr/bin/env bash
set -uo pipefail
. "$LIB"
lifecycle_install_traps
"$WORK/parent.sh" "$WORK/grandchild.pid" &
lifecycle_track \$! "parent"
for _ in \$(seq 1 20); do [ -s "$WORK/grandchild.pid" ] && break; sleep 0.2; done
sleep 60
EOS
chmod +x "$WORK/case_int.sh"
rm -f "$WORK/grandchild.pid"
INT_STATUS="$(python3 - "$WORK/case_int.sh" "$WORK/grandchild.pid" <<'PY'
import os, signal, subprocess, sys, time
script, pidfile = sys.argv[1], sys.argv[2]
p = subprocess.Popen(["bash", script], start_new_session=True)
for _ in range(40):
    if os.path.exists(pidfile) and open(pidfile).read().strip():
        break
    time.sleep(0.2)
time.sleep(0.5)
os.killpg(os.getpgid(p.pid), signal.SIGINT)   # exactly what Ctrl+C does
print(p.wait())
PY
)"
GRANDCHILD="$(cat "$WORK/grandchild.pid" 2>/dev/null || true)"
ok "$(wait_gone "$GRANDCHILD" && echo 0 || echo 1)" "grandchild is gone after SIGINT (pid $GRANDCHILD)"
ok "$([ "$INT_STATUS" = "130" ] && echo 0 || echo 1)" "interrupted script reports 130, not a silent 0 (got $INT_STATUS)"

echo "### 4. SIGTERM (timeout / CI cancellation) -> nothing survives"
run_case 'sleep 60' "TERM"
ok "$(wait_gone "$GRANDCHILD" && echo 0 || echo 1)" "grandchild is gone after SIGTERM (pid $GRANDCHILD)"
ok "$([ "$CASE_STATUS" = "143" ] && echo 0 || echo 1)" "terminated script reports 143 (got $CASE_STATUS)"

echo "### 5. a process that was never tracked is NOT killed"
# Same command line as the tracked child, different owner: this stands in
# for another project's `next-server`/`pnpm`. Targeting by NAME instead of
# by tracked pid would kill it, which is the bug this asserts against.
"$WORK/parent.sh" "$WORK/stranger.pid" &
STRANGER=$!
for _ in $(seq 1 40); do [ -s "$WORK/stranger.pid" ] && break; sleep 0.2; done
STRANGER_CHILD="$(cat "$WORK/stranger.pid" 2>/dev/null || true)"
run_case 'exit 0' ""
sleep 1
ok "$(alive "$STRANGER" && echo 0 || echo 1)" "an untracked identical process survives cleanup (pid $STRANGER)"
ok "$(alive "$STRANGER_CHILD" && echo 0 || echo 1)" "its child survives too (pid $STRANGER_CHILD)"
kill -KILL "$STRANGER_CHILD" "$STRANGER" 2>/dev/null || true
wait "$STRANGER" 2>/dev/null || true

echo "### 6. cleanup is idempotent and hook failures do not mask the result"
cat > "$WORK/idem.sh" <<EOS
#!/usr/bin/env bash
set -uo pipefail
. "$LIB"
lifecycle_on_cleanup 'false'
lifecycle_on_cleanup 'echo hook-ran > "$WORK/hook.out"'
lifecycle_cleanup
lifecycle_cleanup
lifecycle_cleanup
exit 7
EOS
chmod +x "$WORK/idem.sh"
rm -f "$WORK/hook.out"
"$WORK/idem.sh" >"$WORK/idem.log" 2>&1
IDEM=$?
ok "$([ "$IDEM" = "7" ] && echo 0 || echo 1)" "a failing cleanup hook does not overwrite the script's exit status (got $IDEM)"
ok "$([ -f "$WORK/hook.out" ] && echo 0 || echo 1)" "a later hook still runs after an earlier hook fails"
ok "$([ "$(grep -c 'hook-ran' "$WORK/hook.out" 2>/dev/null || echo 0)" = "1" ] && echo 0 || echo 1)" "three cleanup calls run the hooks exactly once"

echo "### 7. re-sourcing the library does not discard a live registry"
cat > "$WORK/resource.sh" <<EOS
#!/usr/bin/env bash
set -uo pipefail
. "$LIB"
sleep 600 & TRACKED=\$!
lifecycle_track \$TRACKED "first"
. "$LIB"
echo \$TRACKED > "$WORK/tracked.pid"
lifecycle_cleanup
EOS
chmod +x "$WORK/resource.sh"
"$WORK/resource.sh" >/dev/null 2>&1
RESOURCED="$(cat "$WORK/tracked.pid" 2>/dev/null || true)"
ok "$(wait_gone "$RESOURCED" && echo 0 || echo 1)" "a pid tracked before re-sourcing is still cleaned up (pid $RESOURCED)"

echo
echo "passed=$PASS failed=$FAIL"
[ "$FAIL" -eq 0 ]

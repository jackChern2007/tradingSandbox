#!/usr/bin/env bash
# verify_boundary.sh - prove the supervisor CANNOT read forecasts/.
#
# Run AS THE SUPERVISOR USER, by Jack, before launch and after any permission
# change. It asserts failure: every read under forecasts/ must be refused with
# "Permission denied". Exit 0 only when the boundary holds.
#
#   sudo -u <supervisor-user> ./verify_boundary.sh study
#
# The supervisor cannot run this on itself and trust the answer - a process
# cannot verify its own sandbox from inside it. Jack runs it.

set -u
ROOT="${1:-study}"
me="$(id -un)"
fail=0

say()  { printf '%s\n' "$*"; }
bad()  { say "  FAIL  $*"; fail=1; }
ok()   { say "  ok    $*"; }

say "verify_boundary: user=$me root=$ROOT"

if [ "$(id -u)" -eq 0 ]; then
  bad "running as root - root bypasses filesystem permissions, so this check proves nothing"
fi

# 1. health/ must be readable, or we are the wrong user / wrong path.
if ls "$ROOT/health" >/dev/null 2>&1; then
  ok "health/ is listable (right user, right path)"
else
  bad "health/ is not listable - wrong user or wrong root; the check below would be meaningless"
fi

# 2. forecasts/ itself must refuse a listing.
out="$(ls "$ROOT/forecasts" 2>&1)"; rc=$?
if [ $rc -eq 0 ]; then
  bad "forecasts/ is LISTABLE"
elif printf '%s' "$out" | grep -qi "permission denied"; then
  ok "forecasts/ listing refused (EACCES)"
else
  bad "forecasts/ listing failed for a reason other than permissions: $out"
fi

# 3. every file under forecasts/ must refuse a read.
for f in forecasts.jsonl resolutions.jsonl; do
  out="$(cat "$ROOT/forecasts/$f" 2>&1)"; rc=$?
  if [ $rc -eq 0 ]; then
    bad "forecasts/$f is READABLE"
  elif printf '%s' "$out" | grep -qi "permission denied"; then
    ok "forecasts/$f read refused (EACCES)"
  else
    # ENOENT here is acceptable only because the directory itself is unsearchable
    say "  note  forecasts/$f: $out"
  fi
done
out="$(ls "$ROOT/forecasts/context" 2>&1)"; rc=$?
if [ $rc -eq 0 ]; then
  bad "forecasts/context/ is LISTABLE"
elif printf '%s' "$out" | grep -qi "permission denied"; then
  ok "forecasts/context/ listing refused (EACCES)"
else
  say "  note  forecasts/context/: $out"
fi

# 4. nothing in health/ may be writable by the supervisor either - it reads only.
if [ -w "$ROOT/health/health.jsonl" ] 2>/dev/null; then
  bad "health/health.jsonl is WRITABLE by $me (supervisor must be read-only)"
else
  ok "health/health.jsonl not writable"
fi

if [ $fail -eq 0 ]; then
  say "BOUNDARY HOLDS for user $me"
  exit 0
else
  say "BOUNDARY BROKEN - do not launch"
  exit 1
fi

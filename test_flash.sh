#!/usr/bin/env bash
# Integration test for flash CLI.
# Creates a temp repo with a worktree, runs all commands, and verifies results.
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'
PASS=0
FAIL=0

assert_eq() {
    local desc="$1" expected="$2" actual="$3"
    if [ "$expected" = "$actual" ]; then
        echo -e "${GREEN}PASS${NC}: $desc"
        PASS=$((PASS + 1))
    else
        echo -e "${RED}FAIL${NC}: $desc"
        echo "  expected: $expected"
        echo "  actual:   $actual"
        FAIL=$((FAIL + 1))
    fi
}

assert_contains() {
    local desc="$1" needle="$2" haystack="$3"
    if echo "$haystack" | grep -q "$needle"; then
        echo -e "${GREEN}PASS${NC}: $desc"
        PASS=$((PASS + 1))
    else
        echo -e "${RED}FAIL${NC}: $desc"
        echo "  expected to contain: $needle"
        echo "  actual: $haystack"
        FAIL=$((FAIL + 1))
    fi
}

assert_file_content() {
    local desc="$1" file="$2" expected="$3"
    local actual
    actual=$(cat "$file")
    assert_eq "$desc" "$expected" "$actual"
}

# --- Setup ---
TMPDIR=$(mktemp -d)
REPO="$TMPDIR/repo"
WT="$TMPDIR/worktree"
trap "rm -rf $TMPDIR" EXIT

mkdir "$REPO"
cd "$REPO"
git init -q
echo "original" > file.txt
git add . && git commit -q -m "initial"

git worktree add -q "$WT" -b test-branch
echo "committed-change" > "$WT/new-file.txt"
(cd "$WT" && git add . && git commit -q -m "worktree commit")
# Add uncommitted change in worktree
echo "uncommitted-change" > "$WT/uncommitted.txt"
echo "committed-change
extra-line" > "$WT/new-file.txt"

# Dirty the main checkout
echo "dirty" > "$REPO/dirty.txt"

echo ""
echo "=== Test 1: flash into ==="
cd "$REPO"
output=$(flash into test-branch 2>&1)
assert_contains "stash message" "Stashing" "$output"
assert_contains "flash message" "Flashed into" "$output"

echo ""
echo "=== Test 2: flash status ==="
output=$(flash status 2>&1)
assert_contains "shows branch" "test-branch" "$output"
assert_contains "shows original" "main" "$output"

echo ""
echo "=== Test 3: uncommitted changes copied in ==="
assert_file_content "uncommitted file present" "$REPO/uncommitted.txt" "uncommitted-change"
assert_file_content "modified file has worktree state" "$REPO/new-file.txt" "committed-change
extra-line"

echo ""
echo "=== Test 4: double flash refused ==="
output=$(flash into test-branch 2>&1 || true)
assert_contains "refuses double flash" "Already flashed" "$output"

echo ""
echo "=== Test 5: flash apply (unstaged files) ==="
echo "flash-fix" >> "$REPO/new-file.txt"
output=$(flash apply 2>&1)
assert_contains "apply syncs files" "Synced" "$output"
assert_file_content "worktree gets unstaged fix" "$WT/new-file.txt" "committed-change
extra-line
flash-fix"

echo ""
echo "=== Test 6: flash apply (commits) ==="
echo "committed-fix" > "$REPO/committed-file.txt"
(cd "$REPO" && git add . && git commit -q -m "fix during flash")
output=$(flash apply 2>&1)
assert_contains "cherry-pick message" "Cherry-picked 1 commit" "$output"
# The committed file should now be in the worktree's git history
wt_has_commit=$(cd "$WT" && git log --oneline | grep -c "fix during flash" || true)
assert_eq "commit landed in worktree" "1" "$wt_has_commit"

echo ""
echo "=== Test 7: flash out --apply (commits + unstaged) ==="
echo "second-committed" > "$REPO/second.txt"
(cd "$REPO" && git add . && git commit -q -m "second fix")
echo "loose-change" > "$REPO/loose.txt"
output=$(flash out --apply 2>&1)
assert_contains "cherry-pick on exit" "Cherry-picked 1 commit" "$output"
assert_contains "sync on exit" "Synced" "$output"
assert_contains "back on main" "Back on" "$output"

# Verify commit landed in worktree
wt_has_second=$(cd "$WT" && git log --oneline | grep -c "second fix" || true)
assert_eq "second commit in worktree" "1" "$wt_has_second"
# Verify unstaged file synced
assert_file_content "loose file in worktree" "$WT/loose.txt" "loose-change"

# Verify restoration
assert_eq "back on main" "main" "$(git branch --show-current)"
assert_file_content "stash restored" "$REPO/dirty.txt" "dirty"
assert_eq "no .flash dir" "false" "$([ -d "$REPO/.flash" ] && echo true || echo false)"
assert_eq "temp branch deleted" "" "$(git branch --list 'flash/*')"

echo ""
echo "=== Test 8: flash out --discard ==="
flash into test-branch >/dev/null 2>&1
echo "throwaway" >> "$REPO/new-file.txt"
wt_before=$(cd "$WT" && git log --oneline | wc -l | tr -d ' ')
flash out --discard >/dev/null 2>&1
wt_after=$(cd "$WT" && git log --oneline | wc -l | tr -d ' ')
assert_eq "discard doesn't touch worktree" "$wt_before" "$wt_after"

echo ""
echo "=== Test 9: flash from worktree directory ==="
cd "$WT"
flash into test-branch >/dev/null 2>&1
output=$(flash status 2>&1)
assert_contains "works from worktree" "Flashed into: test-branch" "$output"
canonical_branch=$(cd "$REPO" && git branch --show-current)
assert_eq "canonical checkout switched" "flash/test-branch" "$canonical_branch"
flash out --discard >/dev/null 2>&1

echo ""
echo "================================"
echo -e "Results: ${GREEN}$PASS passed${NC}, ${RED}$FAIL failed${NC}"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1

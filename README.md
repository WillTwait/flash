<h1 align="center">⚡ flash</h1>

<p align="center">
  Preview any git worktree branch from your main checkout — without losing your place.
</p>

---

## Why

Your main checkout is where everything actually runs — dev server, database, ports, environment. Worktrees are great for parallel development (especially with AI agents cranking out branches), but when you want to *test* what they produced, you're stuck doing a manual stash-checkout-test-restore dance. One wrong move and you've lost uncommitted work or forgotten which branch you were on.

`flash` wraps that entire dance in a single command with a state file, so nothing gets lost.

## How

```bash
flash into my-worktree    # stash + checkout worktree branch
# test, poke around, make fixes
flash apply                # sync fixes back to worktree
flash out                  # restore original branch + pop stash
```

Everything is tracked in a state file so nothing gets lost.

## Install

```bash
uv tool install flash-git            # from PyPI (soon)
uv tool install ~/Developer/flash    # from local clone
```

## Commands

### `flash into [name]`

Switch to a worktree's branch on your canonical (non-worktree) checkout.

- Stashes uncommitted changes automatically
- Creates a temp branch `flash/<branch>` so it doesn't conflict with the worktree
- Pass a worktree name/branch, or omit for an **fzf picker**
- Works from anywhere — even from inside a worktree

### `flash out [--apply | --discard]`

End the flash and go home.

| Flag | Behavior |
|------|----------|
| `--apply` | Sync changes to the worktree directory |
| `--discard` | Throw away changes |
| _(neither)_ | Prompt: `[a]pply / [d]iscard` |

Restores your original branch, pops the stash, deletes the temp branch, and cleans up the state file.

### `flash apply`

Sync current changes to the worktree **without ending the flash**. Useful for iterating:

```
flash into feature → test → fix → flash apply → test again → flash out
```

### `flash status`

Read-only. Shows current state or "Not flashed in." Safe to call anytime.

## How it works

On `flash into`, a state file is written to `<repo>/.flash/state.json` (auto-excluded from git). It tracks:

- Original branch and HEAD SHA
- Stash SHA (looked up by SHA, not index — safe even if you stash other things)
- Worktree path and branch

On `flash out`, everything is restored and `.flash/` is deleted.

## Safety

| Risk | Mitigation |
|------|------------|
| Losing stashed changes | Tracked by SHA, not index position |
| Double flash | Refused if already flashed in |
| Branch conflict with worktree | Uses `flash/<branch>` temp branch |
| Interrupted mid-operation | State file has everything needed for manual recovery |
| Non-interactive (CI/agents) | Defaults to `--discard` with a warning |

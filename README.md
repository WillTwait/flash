<h1 align="center">⚡ flash</h1>

<p align="center">
  Preview any git worktree branch from your main checkout — without losing your place.
</p>

---

## Why

Your main checkout is where everything actually runs — dev server, database, ports, environment. Worktrees are great for parallel development (especially with AI agents cranking out branches), but when you want to *test* what they produced, you're stuck doing a manual stash-checkout-test-restore dance. One wrong move and you've lost uncommitted work or forgotten which branch you were on.

`flash` wraps that entire dance in a single command with a state file, so nothing gets lost.

## Install

```bash
uv tool install flash-git            # from PyPI (soon)
uv tool install ~/Developer/flash    # from local clone
```

## Commands

| Command | Description |
|---------|-------------|
| `flash into [name]` | Switch to a worktree's branch (or open fzf picker) |
| `flash out` | End flash, restore original branch + stash |
| `flash out --apply` | End flash, sync changes to worktree first |
| `flash out --discard` | End flash, throw away changes |
| `flash apply` | Sync changes to worktree without ending flash |
| `flash status` | Show current flash state |

## Workflow

```bash
flash into my-worktree    # stash + checkout worktree branch
# test, poke around, make fixes
flash apply                # sync fixes back to worktree
# test some more
flash out                  # restore original branch + pop stash
```

## Details

**`flash into`** stashes uncommitted changes, creates a temp branch `flash/<branch>` at the worktree's HEAD, and checks it out. Pass a worktree name or branch, or omit for an **fzf picker**. Works from anywhere — even from inside a worktree.

**`flash out`** restores your original branch, pops the stash, deletes the temp branch, and removes the state file. If you have uncommitted changes, it prompts: `[a]pply / [d]iscard` (non-interactive defaults to discard).

**`flash apply`** syncs your current changes to the worktree directory without ending the flash. Useful for iterating: test → fix → apply → test again → flash out.

## How it works

On `flash into`, a state file is written to `<repo>/.flash/state.json` (auto-excluded from git). It tracks your original branch, HEAD SHA, stash SHA, and worktree path. On `flash out`, everything is restored and `.flash/` is deleted.

Stashes are tracked by SHA (not index position), so they're safe even if you stash other things while flashed in.

## Safety

| Risk | Mitigation |
|------|------------|
| Losing stashed changes | Tracked by SHA, not index position |
| Double flash | Refused if already flashed in |
| Branch conflict with worktree | Uses `flash/<branch>` temp branch |
| Interrupted mid-operation | State file has everything needed for manual recovery |
| Non-interactive (CI/agents) | Defaults to `--discard` with a warning |

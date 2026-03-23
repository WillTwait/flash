<h1 align="center">⚡ flash</h1>

<p align="center">
  Preview any git worktree branch from your main checkout — without losing your place.
</p>

---

## Why

Your main checkout is where everything actually runs — dev server, database, ports, environment. Worktrees are great for parallel development (especially with AI agents cranking out branches), but when you want to *test* what they produced, you're stuck doing a manual stash-checkout-test-restore dance. One wrong move and you've lost uncommitted work or forgotten which branch you were on.

`flash` wraps that entire dance in a single command with a state file, so nothing gets lost.

Inspired by [Conductor's Spotlight]([url](https://docs.conductor.build/guides/spotlight-testing)).

## Install

First, clone the repo. Then,

```bash
uv tool install ~/<path-to-flash>    # from local clone
```

## Commands

| Command | Alias | Description |
|---------|-------|-------------|
| `flash into [name]` | `flash i` | Switch to a worktree's branch (or open fzf picker) |
| `flash out` | `flash o` | End flash, restore original branch + stash |
| `flash out --apply` | | End flash, send changes to worktree first |
| `flash out --discard` | | End flash, throw away changes |
| `flash apply` | `flash a` | Send changes to worktree without ending flash |
| `flash status` | `flash st` | Show current flash state |

## Workflow

```bash
flash into my-worktree    # stash + checkout worktree branch
# test, poke around, make fixes, commit
flash apply                # cherry-pick commits + sync files back to worktree
# test some more
flash out                  # restore original branch + pop stash
```

## Details

**`flash into`** stashes uncommitted changes, creates a temp branch `flash/<branch>` at the worktree's HEAD, and checks it out. The worktree's uncommitted changes are also copied over, so you see the full working state. Pass a worktree name or branch, or omit for an **fzf picker**. Works from anywhere — even from inside a worktree.

**`flash out`** restores your original branch, pops the stash, deletes the temp branch, and removes the state file. If you made changes during the flash, it prompts: `[a]pply / [d]iscard` (non-interactive defaults to discard).

**`flash apply`** sends your work back to the worktree without ending the flash. Commits are cherry-picked into the worktree's branch history. Uncommitted file changes are copied over. You can apply multiple times as you iterate.

## How it works

On `flash into`, a state file is written to `<repo>/.flash/state.json` (auto-excluded from git). It tracks your original branch, HEAD SHA, stash SHA, and worktree path. On `flash out`, everything is restored and `.flash/` is deleted.

**Apply strategy**: Before applying, the worktree's uncommitted state is saved via `git stash create` (a read-only backup). The worktree is then cleaned, commits are cherry-picked, and uncommitted files are copied. If anything fails, the stash SHA is printed for manual recovery.

Stashes are tracked by SHA (not index position), so they're safe even if you stash other things while flashed in.

## Safety

| Risk | Mitigation |
|------|------------|
| Losing stashed changes | Tracked by SHA, not index position |
| Losing worktree state on apply | `git stash create` backup before any destructive op |
| Double flash | Refused if already flashed in |
| Branch conflict with worktree | Uses `flash/<branch>` temp branch |
| Interrupted mid-operation | State file has everything needed for manual recovery |
| Non-interactive (CI/agents) | Defaults to `--discard` with a warning |

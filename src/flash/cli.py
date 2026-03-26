"""Typer CLI entry point for flash."""

from __future__ import annotations

import sys
from pathlib import Path

import typer

from flash.core import (
    FlashError,
    checkout_branch,
    cherry_pick_to_worktree,
    clean_working_tree,
    create_and_checkout_temp_branch,
    delete_branch,
    ensure_git_exclude,
    fzf_pick_worktree,
    get_canonical_root,
    get_commits_since,
    get_current_branch,
    get_head_sha,
    is_dirty,
    list_worktrees,
    pop_stash_by_sha,
    resolve_worktree,
    stash_changes,
    stash_create,
    sync_changes,
)
from flash.state import FlashState, clear_state, now_iso, read_state, write_state

app = typer.Typer(
    help="Safely swap your main checkout to a worktree branch and back.",
    no_args_is_help=True,
)


def _err(msg: str) -> None:
    typer.secho(msg, fg=typer.colors.RED, err=True)


def _ok(msg: str) -> None:
    typer.secho(msg, fg=typer.colors.GREEN)


def _info(msg: str) -> None:
    typer.secho(msg, fg=typer.colors.YELLOW)


def _apply_to_worktree(state: FlashState, canonical_root: str) -> None:
    """Cherry-pick commits and sync unstaged files to the worktree.

    Strategy:
    1. Safety backup: `git stash create` in worktree (read-only)
    2. Clean the worktree so cherry-pick can't conflict
    3. Cherry-pick new commits onto clean worktree
    4. Copy uncommitted files from canonical → worktree
    5. Update state so next apply only picks new commits
    """

    commits = get_commits_since(state.flash_base_sha, cwd=canonical_root)
    has_uncommitted = is_dirty(cwd=canonical_root)

    if not commits and not has_uncommitted:
        _info("No changes to apply.")
        return

    # Safety backup of worktree state (read-only, no side effects)
    safety_sha = stash_create(cwd=state.worktree_path)

    try:
        if commits:
            # Clean worktree for conflict-free cherry-pick
            clean_working_tree(cwd=state.worktree_path)
            cherry_pick_to_worktree(commits, state.worktree_path)
            _ok(f"Cherry-picked {len(commits)} commit(s) to worktree.")

            # Update base SHA so next apply only picks new commits
            new_base = get_head_sha(cwd=canonical_root)
            state.flash_base_sha = new_base
            write_state(state)

        if has_uncommitted:
            synced = sync_changes("HEAD", canonical_root, state.worktree_path)
            if synced:
                _ok(f"Synced {len(synced)} uncommitted file(s) to worktree:")
                for f in synced:
                    typer.echo(f"  {f}")

    except FlashError:
        if safety_sha:
            _err(f"Worktree state backed up as stash {safety_sha}")
            _err(
                f"  Recover with: cd {state.worktree_path} && git stash apply {safety_sha}"
            )
        raise


def _complete_worktree_name(incomplete: str) -> list[str]:
    """Tab-completion for worktree names."""
    try:
        canonical_root = get_canonical_root()
    except FlashError:
        return []
    worktrees = list_worktrees(cwd=canonical_root)
    names = []
    for wt in worktrees:
        if wt.is_bare or wt.path == canonical_root:
            continue
        dir_name = Path(wt.path).name
        if incomplete in dir_name:
            names.append(dir_name)
        elif incomplete in wt.branch:
            names.append(wt.branch)
    return names


@app.command()
def into(
    name: str | None = typer.Argument(
        None,
        help="Worktree directory name or branch name",
        autocompletion=_complete_worktree_name,
    ),
) -> None:
    """Flash into a worktree branch on the canonical checkout. [magenta]\\[alias: i][/magenta]"""
    try:
        canonical_root = get_canonical_root()
    except FlashError as e:
        _err(str(e))
        raise typer.Exit(1)

    # Check if already flashed in
    existing = read_state(canonical_root)
    if existing is not None:
        _err(f"Already flashed into '{existing.flash_branch}'. Run 'flash out' first.")
        raise typer.Exit(1)

    # Resolve the target worktree
    if name is None:
        wt = fzf_pick_worktree(canonical_root)
        if wt is None:
            _err("No worktree selected.")
            raise typer.Exit(1)
    else:
        wt = resolve_worktree(name, cwd=canonical_root)
        if wt is None:
            # Show available worktrees
            worktrees = list_worktrees(cwd=canonical_root)
            _err(f"Could not resolve '{name}' to a worktree.")
            _info("Available worktrees:")
            for w in worktrees:
                if not w.is_bare and w.path != canonical_root:
                    typer.echo(f"  {w.branch}  ({w.path})")
            raise typer.Exit(1)

    # Don't flash into the canonical root itself
    if wt.path == canonical_root:
        _err("Cannot flash into the canonical checkout itself.")
        raise typer.Exit(1)

    try:
        # Record current state
        original_branch = get_current_branch(cwd=canonical_root)
        original_head_sha = get_head_sha(cwd=canonical_root)

        # Stash if dirty
        stash_sha = None
        if is_dirty(cwd=canonical_root):
            _info("Stashing uncommitted changes...")
            stash_sha = stash_changes(f"flash: before {wt.branch}", cwd=canonical_root)

        # Create and checkout temp branch at worktree branch's HEAD
        temp_branch = f"flash/{wt.branch}"
        _info(f"Creating temp branch '{temp_branch}' at {wt.head[:8]}...")
        create_and_checkout_temp_branch(temp_branch, wt.head, cwd=canonical_root)

        # Copy uncommitted changes from worktree into canonical checkout
        wt_synced = sync_changes("HEAD", wt.path, canonical_root)
        if wt_synced:
            _info(f"Copied {len(wt_synced)} uncommitted file(s) from worktree.")

        # Ensure .flash/ is excluded from git
        ensure_git_exclude(canonical_root)

        # Write state — flash_base_sha is the worktree's HEAD (temp branch start)
        state = FlashState(
            original_branch=original_branch,
            flash_branch=wt.branch,
            temp_branch=temp_branch,
            worktree_path=wt.path,
            canonical_root=canonical_root,
            original_head_sha=original_head_sha,
            flash_base_sha=wt.head,
            stash_sha=stash_sha,
            started_at=now_iso(),
        )
        write_state(state)

        _ok(f"Flashed into '{wt.branch}'. Run 'flash out' when done.")

    except FlashError as e:
        _err(str(e))
        raise typer.Exit(1)


@app.command()
def out(
    apply: bool = typer.Option(
        False, "--apply", help="Apply changes to worktree before exiting"
    ),
    discard: bool = typer.Option(
        False, "--discard", help="Discard changes made during flash"
    ),
) -> None:
    """End flash session and restore original state. [magenta]\\[alias: o][/magenta]"""
    try:
        canonical_root = get_canonical_root()
    except FlashError as e:
        _err(str(e))
        raise typer.Exit(1)

    state = read_state(canonical_root)
    if state is None:
        _err("Not currently flashed in.")
        raise typer.Exit(1)

    try:
        # Check for any changes (commits or unstaged)
        commits = get_commits_since(state.flash_base_sha, cwd=canonical_root)
        has_unstaged = is_dirty(cwd=canonical_root)
        has_changes = bool(commits) or has_unstaged

        if has_changes:
            if apply and discard:
                _err("Cannot use both --apply and --discard.")
                raise typer.Exit(1)

            if not apply and not discard:
                # Interactive: prompt user
                if sys.stdin.isatty():
                    _info("You have changes during this flash.")
                    if commits:
                        _info(f"  {len(commits)} commit(s)")
                    if has_unstaged:
                        _info("  Uncommitted file changes")
                    choice = (
                        typer.prompt(
                            "[a]pply to worktree / [d]iscard",
                            default="d",
                        )
                        .strip()
                        .lower()
                    )
                    apply = choice in ("a", "apply")
                    discard = not apply
                else:
                    _info("Non-interactive mode: discarding changes.")
                    discard = True

            if apply:
                _apply_to_worktree(state, canonical_root)

        # Discard any local changes before switching branches
        if is_dirty(cwd=canonical_root):
            clean_working_tree(cwd=canonical_root)

        # Restore original branch
        _info(f"Checking out '{state.original_branch}'...")
        checkout_branch(state.original_branch, cwd=canonical_root)

        # Delete temp branch
        delete_branch(state.temp_branch, cwd=canonical_root)

        # Pop stash if we stashed
        if state.stash_sha:
            _info("Restoring stashed changes...")
            if not pop_stash_by_sha(state.stash_sha, cwd=canonical_root):
                _err(
                    f"Warning: Could not find stash with SHA {state.stash_sha}. "
                    f"Your changes may still be in the stash list."
                )

        # Clean up state
        clear_state(canonical_root)

        _ok(f"Back on '{state.original_branch}'.")

    except FlashError as e:
        _err(str(e))
        _err("State file preserved for manual recovery.")
        raise typer.Exit(1)


@app.command()
def status() -> None:
    """Show current flash state. [magenta]\\[alias: st][/magenta]"""
    try:
        canonical_root = get_canonical_root()
    except FlashError as e:
        _err(str(e))
        raise typer.Exit(1)

    state = read_state(canonical_root)
    if state is None:
        typer.echo("Not flashed in.")
        raise typer.Exit(0)

    typer.echo(f"Flashed into: {state.flash_branch}")
    typer.echo(f"Original branch: {state.original_branch}")
    typer.echo(f"Temp branch: {state.temp_branch}")
    typer.echo(f"Worktree: {state.worktree_path}")
    typer.echo(f"Started: {state.started_at}")
    if state.stash_sha:
        typer.echo(f"Stash SHA: {state.stash_sha}")

    # Show if there are current changes
    commits = get_commits_since(state.flash_base_sha, cwd=canonical_root)
    has_unstaged = is_dirty(cwd=canonical_root)
    if commits:
        _info(f"{len(commits)} new commit(s) since flash.")
    if has_unstaged:
        _info("Uncommitted file changes.")


@app.command("apply")
def apply_changes() -> None:
    """Push current changes to the worktree without ending the flash. [magenta]\\[alias: a][/magenta]"""
    try:
        canonical_root = get_canonical_root()
    except FlashError as e:
        _err(str(e))
        raise typer.Exit(1)

    state = read_state(canonical_root)
    if state is None:
        _err("Not currently flashed in.")
        raise typer.Exit(1)

    try:
        _apply_to_worktree(state, canonical_root)
    except FlashError as e:
        _err(str(e))
        raise typer.Exit(1)


@app.command("sync")
def sync_from_worktree() -> None:
    """Pull uncommitted worktree changes into the canonical checkout."""
    try:
        canonical_root = get_canonical_root()
    except FlashError as e:
        _err(str(e))
        raise typer.Exit(1)

    state = read_state(canonical_root)
    if state is None:
        _err("Not currently flashed in.")
        raise typer.Exit(1)

    try:
        synced = sync_changes("HEAD", state.worktree_path, canonical_root)
        if not synced:
            _info("No changes to sync from worktree.")
            raise typer.Exit(0)

        _ok(f"Synced {len(synced)} file(s) from {state.worktree_path}")

    except FlashError as e:
        _err(str(e))
        raise typer.Exit(1)


# Hidden short aliases
app.command("i", hidden=True)(into)
app.command("o", hidden=True)(out)
app.command("st", hidden=True)(status)
app.command("a", hidden=True)(apply_changes)
app.command("s", hidden=True)(sync_from_worktree)


if __name__ == "__main__":
    app()

"""Typer CLI entry point for flash."""

from __future__ import annotations

import sys

import typer

from flash.core import (
    FlashError,
    checkout_branch,
    create_and_checkout_temp_branch,
    delete_branch,
    ensure_git_exclude,
    fzf_pick_worktree,
    get_canonical_root,
    get_current_branch,
    get_head_sha,
    has_changes_against,
    is_dirty,
    list_worktrees,
    pop_stash_by_sha,
    resolve_worktree,
    stash_changes,
    sync_changes_from_dir,
    sync_changes_to_dir,
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


@app.command()
def into(
    name: str | None = typer.Argument(
        None, help="Worktree directory name or branch name"
    ),
) -> None:
    """Flash into a worktree branch on the canonical checkout."""
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

        # Ensure .flash/ is excluded from git
        ensure_git_exclude(canonical_root)

        # Write state
        state = FlashState(
            original_branch=original_branch,
            flash_branch=wt.branch,
            temp_branch=temp_branch,
            worktree_path=wt.path,
            canonical_root=canonical_root,
            original_head_sha=original_head_sha,
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
    """End flash session and restore original state."""
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
        # Check for changes made during flash
        has_changes = has_changes_against(state.temp_branch, cwd=canonical_root)

        if has_changes:
            if apply and discard:
                _err("Cannot use both --apply and --discard.")
                raise typer.Exit(1)

            if not apply and not discard:
                # Interactive: prompt user
                if sys.stdin.isatty():
                    _info("You have uncommitted changes.")
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
                _info("Applying changes to worktree...")
                count = sync_changes_to_dir(
                    state.temp_branch, canonical_root, state.worktree_path
                )
                if count:
                    _ok(f"Synced {count} file(s) to {state.worktree_path}")
                else:
                    _info("No changes to apply.")

        # Discard any local changes before switching branches
        if is_dirty(cwd=canonical_root):
            from flash.core import run_git

            run_git("checkout", ".", cwd=canonical_root)
            run_git("clean", "-fd", cwd=canonical_root)

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
    """Show current flash state."""
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
    has_changes = is_dirty(cwd=canonical_root)
    if has_changes:
        _info("Current working tree has modifications.")


@app.command("apply")
def apply_changes() -> None:
    """Push current changes to the worktree without ending the flash."""
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
        count = sync_changes_to_dir(
            state.temp_branch, canonical_root, state.worktree_path
        )
        if not count:
            _info("No changes to apply.")
            raise typer.Exit(0)

        _ok(f"Synced {count} file(s) to {state.worktree_path}")

    except FlashError as e:
        _err(str(e))
        raise typer.Exit(1)


@app.command("sync")
def sync_changes() -> None:
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
        count = sync_changes_from_dir(state.worktree_path, canonical_root)
        if not count:
            _info("No changes to sync from worktree.")
            raise typer.Exit(0)

        _ok(f"Synced {count} file(s) from {state.worktree_path}")

    except FlashError as e:
        _err(str(e))
        raise typer.Exit(1)


if __name__ == "__main__":
    app()

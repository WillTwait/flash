"""Typer CLI entry point for flash."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import typer

from flash.core import (
    FlashError,
    _NEW_WORKTREE_SENTINEL,
    checkout_branch,
    cherry_pick_to_worktree,
    clean_working_tree,
    create_and_checkout_temp_branch,
    create_worktree,
    delete_branch,
    ensure_git_exclude,
    fzf_pick_worktree,
    get_canonical_root,
    get_commits_since,
    get_current_branch,
    get_diverged_files,
    get_head_sha,
    is_dirty,
    list_worktrees,
    pop_stash_by_sha,
    resolve_worktree,
    run_git,
    stash_changes,
    stash_create,
    sync_changes,
)
from flash.state import FlashState, clear_state, now_iso, read_state, write_state

from importlib.metadata import version as pkg_version

__version__ = pkg_version("worktree-flash")


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"flash {__version__}")
        raise typer.Exit()


def _main_callback(
    version: bool = typer.Option(
        False, "--version", "-v", callback=_version_callback, is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    pass


app = typer.Typer(
    help="Safely swap your main checkout to a worktree branch and back.",
    no_args_is_help=True,
    callback=_main_callback,
)


def _err(msg: str) -> None:
    typer.secho(msg, fg=typer.colors.RED, err=True)


def _ok(msg: str) -> None:
    typer.secho(msg, fg=typer.colors.GREEN)


def _info(msg: str) -> None:
    typer.secho(msg, fg=typer.colors.YELLOW)


def _human_duration(iso_str: str) -> str:
    """Convert ISO timestamp to a human-readable duration like '2h 15m'."""
    try:
        started = datetime.fromisoformat(iso_str)
        now = datetime.now(timezone.utc)
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        seconds = int((now - started).total_seconds())
        if seconds < 60:
            return f"{seconds}s"
        minutes = seconds // 60
        if minutes < 60:
            return f"{minutes}m"
        hours = minutes // 60
        mins = minutes % 60
        if hours < 24:
            return f"{hours}h {mins}m" if mins else f"{hours}h"
        days = hours // 24
        hrs = hours % 24
        return f"{days}d {hrs}h" if hrs else f"{days}d"
    except (ValueError, TypeError):
        return ""


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
    new: bool = typer.Option(
        False, "--new", "-n", help="Create a new worktree and flash into it"
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

    # Create new worktree
    if new:
        if name is None:
            _err("Worktree name required with --new.")
            raise typer.Exit(1)
        try:
            _info(f"Creating worktree '{name}'...")
            wt = create_worktree(name, cwd=canonical_root)
        except FlashError as e:
            _err(str(e))
            raise typer.Exit(1)
    # Resolve existing worktree
    elif name is None:
        result = fzf_pick_worktree(canonical_root)
        if result is None:
            _err("No worktree selected.")
            raise typer.Exit(1)
        if result == _NEW_WORKTREE_SENTINEL:
            branch_name = typer.prompt("Worktree name")
            if not branch_name.strip():
                _err("Worktree name cannot be empty.")
                raise typer.Exit(1)
            try:
                _info(f"Creating worktree '{branch_name}'...")
                wt = create_worktree(branch_name.strip(), cwd=canonical_root)
            except FlashError as e:
                _err(str(e))
                raise typer.Exit(1)
        else:
            wt = result
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

        # Ensure .flash/ and worktree dir (if inside repo) are excluded from git
        extra_excludes = []
        try:
            wt_rel = str(Path(wt.path).relative_to(canonical_root))
            extra_excludes.append(wt_rel + "/")
        except ValueError:
            pass  # worktree is outside canonical root
        ensure_git_exclude(canonical_root, extra_excludes)

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
                    _info("You have unapplied changes:")
                    if commits:
                        log_result = run_git(
                            "log", "--oneline",
                            f"{state.flash_base_sha}..HEAD",
                            cwd=canonical_root,
                        )
                        typer.echo(f"  {len(commits)} commit(s):")
                        for line in log_result.stdout.strip().splitlines():
                            typer.echo(f"    {line}")
                    if has_unstaged:
                        diff_stat = run_git(
                            "diff", "--stat", "HEAD",
                            cwd=canonical_root,
                        )
                        if diff_stat.stdout.strip():
                            typer.echo(diff_stat.stdout, nl=False)
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

    # Header with duration
    duration = _human_duration(state.started_at)
    duration_str = f" ({duration} ago)" if duration else ""
    typer.echo(f"Flashed into: {state.flash_branch}{duration_str}")
    typer.echo(f"Original branch: {state.original_branch}")
    typer.echo(f"Worktree: {state.worktree_path}")
    if state.stash_sha:
        typer.echo(f"Stash SHA: {state.stash_sha}")
    typer.echo()

    # Outgoing: commits + diverged uncommitted files
    commits = get_commits_since(state.flash_base_sha, cwd=canonical_root)
    unapplied_files, unsynced_files = get_diverged_files(
        canonical_root, state.worktree_path
    )

    if commits or unapplied_files:
        _info("Unapplied (flash apply):")
        if commits:
            log_result = run_git(
                "log", "--oneline",
                f"{state.flash_base_sha}..HEAD",
                cwd=canonical_root,
            )
            typer.echo(f"  {len(commits)} commit(s):")
            for line in log_result.stdout.strip().splitlines():
                typer.echo(f"    {line}")
        if unapplied_files:
            typer.echo(f"  {len(unapplied_files)} file(s)")
    else:
        typer.echo("No unapplied changes.")

    if unsynced_files:
        _info(f"Unsynced (flash sync): {len(unsynced_files)} file(s) in worktree")
    else:
        typer.echo("No unsynced changes.")


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
    """Pull uncommitted worktree changes into the canonical checkout. [magenta]\\[alias: s][/magenta]"""
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


@app.command("diff")
def diff_changes(
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Show full diff instead of diffstat summary"
    ),
    incoming: bool = typer.Option(
        False, "--incoming", "-i", help="Only show unsynced worktree changes"
    ),
    outgoing: bool = typer.Option(
        False, "--outgoing", "-o", help="Only show unapplied local changes"
    ),
) -> None:
    """Show unapplied and unsynced changes. [magenta]\\[alias: d][/magenta]"""
    try:
        canonical_root = get_canonical_root()
    except FlashError as e:
        _err(str(e))
        raise typer.Exit(1)

    state = read_state(canonical_root)
    if state is None:
        _err("Not currently flashed in.")
        raise typer.Exit(1)

    if incoming and outgoing:
        _err("Cannot use both --incoming and --outgoing.")
        raise typer.Exit(1)

    show_unapplied = not incoming  # show unless --incoming
    show_unsynced = not outgoing   # show unless --outgoing

    color = f"--color={'always' if sys.stdout.isatty() else 'never'}"
    has_output = False

    # Get files that actually differ between canonical and worktree
    unapplied_files, unsynced_files = get_diverged_files(
        canonical_root, state.worktree_path
    )

    # --- Unapplied (what flash apply would send) ---
    if show_unapplied:
        # Commits
        log_result = run_git(
            "log", "--oneline", color,
            f"{state.flash_base_sha}..HEAD",
            cwd=canonical_root,
        )
        has_commits = bool(log_result.stdout.strip())

        # File diff — only for files that actually diverge
        diff_output = ""
        if unapplied_files:
            diff_args = ["diff", color, "HEAD"]
            if not verbose:
                diff_args.append("--stat")
            diff_args.append("--")
            diff_args.extend(unapplied_files)
            diff_result = run_git(*diff_args, cwd=canonical_root)
            diff_output = diff_result.stdout.strip()

        if has_commits or diff_output or unapplied_files:
            has_output = True
            _info("Unapplied (flash apply):")
            if has_commits:
                count = len(log_result.stdout.strip().splitlines())
                typer.echo(f"{count} commit(s):")
                for line in log_result.stdout.strip().splitlines():
                    typer.echo(f"  {line}")
                if diff_output:
                    typer.echo()
            if diff_output:
                typer.echo(diff_output)
            typer.echo()

    # --- Unsynced (what flash sync would pull) ---
    if show_unsynced:
        diff_output = ""
        if unsynced_files:
            diff_args = ["diff", color, "HEAD"]
            if not verbose:
                diff_args.append("--stat")
            diff_args.append("--")
            diff_args.extend(unsynced_files)
            wt_result = run_git(*diff_args, cwd=state.worktree_path)
            diff_output = wt_result.stdout.strip()

        if diff_output or unsynced_files:
            has_output = True
            _info("Unsynced (flash sync):")
            if diff_output:
                typer.echo(diff_output)

    if not has_output:
        _info("No changes.")


@app.command("update")
def check_update() -> None:
    """Check for a newer version of flash. [magenta]\\[alias: u][/magenta]"""
    import json
    import urllib.request

    _info(f"Current version: {__version__}")

    try:
        req = urllib.request.Request(
            "https://api.github.com/repos/WillTwait/flash/releases/latest",
            headers={"Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        latest = data["tag_name"].lstrip("v")
    except Exception:
        _err("Could not check for updates.")
        raise typer.Exit(1)

    if latest == __version__:
        _ok("You're on the latest version.")
        return

    _info(f"Latest version:  {latest}")
    typer.echo()
    typer.echo("Upgrade with:")
    typer.echo("  pipx upgrade worktree-flash")
    typer.echo("  uv tool upgrade worktree-flash")
    typer.echo("  # or git pull if running from source")


# Hidden short aliases
app.command("i", hidden=True)(into)
app.command("o", hidden=True)(out)
app.command("st", hidden=True)(status)
app.command("a", hidden=True)(apply_changes)
app.command("s", hidden=True)(sync_from_worktree)
app.command("d", hidden=True)(diff_changes)
app.command("u", hidden=True)(check_update)


if __name__ == "__main__":
    app()

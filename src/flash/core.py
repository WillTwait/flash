"""Git operations for flash: stash, checkout, diff, apply."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


class FlashError(Exception):
    """Raised when a flash operation fails."""


@dataclass
class Worktree:
    path: str
    branch: str
    head: str
    is_bare: bool = False


def run_git(
    *args: str,
    cwd: str | Path | None = None,
    check: bool = True,
    capture: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a git command and return the result."""
    cmd = ["git", *args]
    result = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=capture,
        text=True,
        check=False,
    )
    if check and result.returncode != 0:
        raise FlashError(f"git {' '.join(args)} failed:\n{result.stderr.strip()}")
    return result


def get_canonical_root(cwd: str | Path | None = None) -> str:
    """Get the canonical (non-worktree) repo root.

    If we're in a worktree, resolve via .git commondir to find the main checkout.
    """
    cwd = cwd or Path.cwd()
    # Get the git dir for current location
    git_dir = run_git("rev-parse", "--git-dir", cwd=cwd).stdout.strip()
    git_dir_path = Path(git_dir) if Path(git_dir).is_absolute() else Path(cwd) / git_dir
    git_dir_path = git_dir_path.resolve()

    # If this is a worktree, .git is a file pointing to the main repo's worktrees dir
    # Use --git-common-dir to find the main repo's .git directory
    common_dir = run_git("rev-parse", "--git-common-dir", cwd=cwd).stdout.strip()
    common_dir_path = (
        Path(common_dir) if Path(common_dir).is_absolute() else Path(cwd) / common_dir
    )
    common_dir_path = common_dir_path.resolve()

    # The canonical root is the parent of the common .git dir
    return str(common_dir_path.parent)


def get_current_branch(cwd: str | Path | None = None) -> str:
    """Get the current branch name."""
    result = run_git("symbolic-ref", "--short", "HEAD", cwd=cwd, check=False)
    if result.returncode != 0:
        # Detached HEAD — return the SHA
        return run_git("rev-parse", "HEAD", cwd=cwd).stdout.strip()
    return result.stdout.strip()


def get_head_sha(cwd: str | Path | None = None) -> str:
    """Get the current HEAD SHA."""
    return run_git("rev-parse", "HEAD", cwd=cwd).stdout.strip()


def is_dirty(cwd: str | Path | None = None) -> bool:
    """Check if the working tree has uncommitted changes."""
    result = run_git("status", "--porcelain", cwd=cwd)
    return bool(result.stdout.strip())


def list_worktrees(cwd: str | Path | None = None) -> list[Worktree]:
    """List all git worktrees."""
    result = run_git("worktree", "list", "--porcelain", cwd=cwd)
    worktrees: list[Worktree] = []
    current: dict[str, str] = {}

    for line in result.stdout.splitlines():
        if not line.strip():
            if current:
                worktrees.append(
                    Worktree(
                        path=current["worktree"],
                        branch=current.get("branch", "").removeprefix("refs/heads/"),
                        head=current.get("HEAD", ""),
                        is_bare="bare" in current,
                    )
                )
                current = {}
            continue
        if line.startswith("worktree "):
            current["worktree"] = line.split(" ", 1)[1]
        elif line.startswith("HEAD "):
            current["HEAD"] = line.split(" ", 1)[1]
        elif line.startswith("branch "):
            current["branch"] = line.split(" ", 1)[1]
        elif line == "bare":
            current["bare"] = "true"
        elif line == "detached":
            pass  # skip detached marker

    # Handle last entry
    if current:
        worktrees.append(
            Worktree(
                path=current["worktree"],
                branch=current.get("branch", "").removeprefix("refs/heads/"),
                head=current.get("HEAD", ""),
                is_bare="bare" in current,
            )
        )

    return worktrees


def resolve_worktree(name: str, cwd: str | Path | None = None) -> Worktree | None:
    """Resolve a name to a worktree by matching directory name or branch name."""
    worktrees = list_worktrees(cwd=cwd)

    for wt in worktrees:
        if wt.is_bare:
            continue
        dir_name = Path(wt.path).name
        if dir_name == name or wt.branch == name:
            return wt

    # Partial match on directory name or branch name
    matches = []
    for wt in worktrees:
        if wt.is_bare:
            continue
        dir_name = Path(wt.path).name
        if name in dir_name or name in wt.branch:
            matches.append(wt)

    if len(matches) == 1:
        return matches[0]

    return None


def fzf_pick_worktree(canonical_root: str) -> Worktree | None:
    """Use fzf to interactively pick a worktree."""
    worktrees = list_worktrees(cwd=canonical_root)
    # Filter out bare and the canonical root itself
    candidates = [
        wt for wt in worktrees if not wt.is_bare and wt.path != canonical_root
    ]

    if not candidates:
        return None

    # Format for fzf: "dir_name  (branch)  path"
    lines = []
    for wt in candidates:
        dir_name = Path(wt.path).name
        lines.append(f"{dir_name}\t{wt.branch}\t{wt.path}")

    fzf_input = "\n".join(lines)

    try:
        result = subprocess.run(
            [
                "fzf",
                "--header=Select a worktree",
                "--delimiter=\t",
                "--with-nth=1,2",
                "--tabstop=4",
            ],
            input=fzf_input,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        raise FlashError("fzf not found. Install fzf or pass a worktree name.")

    if result.returncode != 0:
        return None  # User cancelled

    selected = result.stdout.strip()
    if not selected:
        return None

    parts = selected.split("\t")
    selected_path = parts[2] if len(parts) >= 3 else parts[0]

    for wt in candidates:
        if wt.path == selected_path:
            return wt

    return None


def stash_changes(message: str, cwd: str | Path | None = None) -> str | None:
    """Stash changes and return the stash SHA, or None if nothing to stash."""
    # Get stash list before
    before = run_git("stash", "list", cwd=cwd).stdout.strip()

    run_git("stash", "push", "-m", message, "--include-untracked", cwd=cwd)

    # Get stash list after
    after = run_git("stash", "list", cwd=cwd).stdout.strip()

    if before == after:
        return None  # Nothing was stashed

    # Get the SHA of the most recent stash
    return run_git("rev-parse", "stash@{0}", cwd=cwd).stdout.strip()


def find_stash_by_sha(sha: str, cwd: str | Path | None = None) -> str | None:
    """Find a stash entry by its SHA. Returns the stash ref (e.g., stash@{2})."""
    result = run_git("stash", "list", "--format=%H", cwd=cwd)
    for i, line in enumerate(result.stdout.strip().splitlines()):
        if line.strip() == sha:
            return f"stash@{{{i}}}"
    return None


def pop_stash_by_sha(sha: str, cwd: str | Path | None = None) -> bool:
    """Pop a specific stash entry identified by SHA. Returns True if successful."""
    ref = find_stash_by_sha(sha, cwd=cwd)
    if ref is None:
        return False
    run_git("stash", "pop", ref, cwd=cwd)
    return True


def create_and_checkout_temp_branch(
    temp_branch: str, target_sha: str, cwd: str | Path | None = None
) -> None:
    """Create a temporary branch at target_sha and check it out."""
    # Delete if it already exists (leftover from a crashed session)
    result = run_git("branch", "--list", temp_branch, cwd=cwd)
    if result.stdout.strip():
        run_git("branch", "-D", temp_branch, cwd=cwd)

    run_git("checkout", "-b", temp_branch, target_sha, cwd=cwd)


def checkout_branch(branch: str, cwd: str | Path | None = None) -> None:
    """Check out a branch."""
    run_git("checkout", branch, cwd=cwd)


def delete_branch(branch: str, cwd: str | Path | None = None) -> None:
    """Delete a branch."""
    run_git("branch", "-D", branch, cwd=cwd, check=False)


def get_changed_files(ref: str, cwd: str | Path | None = None) -> list[str]:
    """Get list of files changed between ref and working tree, including untracked."""
    run_git("add", "-A", cwd=cwd)
    result = run_git("diff", "--cached", "--name-only", ref, cwd=cwd)
    run_git("reset", "HEAD", cwd=cwd, check=False)
    return [f for f in result.stdout.strip().splitlines() if f.strip()]


def has_changes_against(ref: str, cwd: str | Path | None = None) -> bool:
    """Check if there are any changes between ref and working tree."""
    return bool(get_changed_files(ref, cwd=cwd))


def sync_changes_to_dir(
    ref: str, canonical_root: str | Path, target_dir: str | Path
) -> int:
    """Copy changed files from canonical checkout to target directory.

    Compares working tree against ref to find changed files, then copies
    each one to the target directory. This is more reliable than patching
    because it handles repeated applies correctly — the target always gets
    the current state of each changed file.

    Returns the number of files synced.
    """
    import shutil

    canonical = Path(canonical_root)
    target = Path(target_dir)
    changed = get_changed_files(ref, cwd=canonical_root)

    if not changed:
        return 0

    count = 0
    for filepath in changed:
        src = canonical / filepath
        dst = target / filepath

        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src), str(dst))
            count += 1
        elif dst.exists():
            # File was deleted in canonical checkout
            dst.unlink()
            count += 1

    return count


def sync_changes_from_dir(worktree_dir: str | Path, canonical_root: str | Path) -> int:
    """Copy changed files from a worktree directory into the canonical checkout.

    Detects uncommitted changes in the worktree (vs its HEAD), then copies
    each changed file into the canonical checkout. Handles deletions too.

    Returns the number of files synced.
    """
    import shutil

    worktree = Path(worktree_dir)
    canonical = Path(canonical_root)
    changed = get_changed_files("HEAD", cwd=worktree_dir)

    if not changed:
        return 0

    count = 0
    for filepath in changed:
        src = worktree / filepath
        dst = canonical / filepath

        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src), str(dst))
            count += 1
        elif dst.exists():
            # File was deleted in worktree
            dst.unlink()
            count += 1

    return count


def ensure_git_exclude(canonical_root: str | Path) -> None:
    """Add .flash/ to .git/info/exclude if not already there."""
    exclude_file = Path(canonical_root) / ".git" / "info" / "exclude"
    exclude_file.parent.mkdir(parents=True, exist_ok=True)

    entry = ".flash/"
    if exclude_file.exists():
        content = exclude_file.read_text()
        if entry in content:
            return
        if not content.endswith("\n"):
            content += "\n"
        content += entry + "\n"
        exclude_file.write_text(content)
    else:
        exclude_file.write_text(entry + "\n")

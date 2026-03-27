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


def create_worktree(branch: str, cwd: str | Path | None = None) -> Worktree:
    """Create a new branch and worktree as a sibling directory.

    Returns the new Worktree.
    """
    cwd = cwd or str(Path.cwd())
    worktree_path = str(Path(cwd).parent / branch)

    if Path(worktree_path).exists():
        raise FlashError(f"Directory already exists: {worktree_path}")

    # Create branch and worktree in one step
    run_git("worktree", "add", "-b", branch, worktree_path, cwd=cwd)

    return Worktree(
        path=worktree_path,
        branch=branch,
        head=get_head_sha(cwd=worktree_path),
    )


_NEW_WORKTREE_SENTINEL = "__new__"


def fzf_pick_worktree(canonical_root: str) -> Worktree | str | None:
    """Use fzf to interactively pick a worktree.

    Returns a Worktree, the _NEW_WORKTREE_SENTINEL string, or None if cancelled.
    """
    worktrees = list_worktrees(cwd=canonical_root)
    # Filter out bare and the canonical root itself
    candidates = [
        wt for wt in worktrees if not wt.is_bare and wt.path != canonical_root
    ]

    # Format for fzf: "dir_name  (branch)  path"
    lines = [f"+ New worktree\t\t{_NEW_WORKTREE_SENTINEL}"]
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

    if selected_path == _NEW_WORKTREE_SENTINEL:
        return _NEW_WORKTREE_SENTINEL

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
    """Get list of files changed between ref and working tree, including untracked.

    Fully read-only — does not mutate the index or working tree.
    """
    files: set[str] = set()

    # Unstaged tracked changes (modified + deleted) vs ref
    result = run_git("diff", "--name-only", ref, cwd=cwd)
    files.update(f.strip() for f in result.stdout.splitlines() if f.strip())

    # Staged changes vs ref
    result = run_git("diff", "--cached", "--name-only", ref, cwd=cwd)
    files.update(f.strip() for f in result.stdout.splitlines() if f.strip())

    # Untracked files
    result = run_git("ls-files", "--others", "--exclude-standard", cwd=cwd)
    files.update(f.strip() for f in result.stdout.splitlines() if f.strip())

    return sorted(files)


def has_changes_against(ref: str, cwd: str | Path | None = None) -> bool:
    """Check if there are any changes between ref and working tree."""
    return bool(get_changed_files(ref, cwd=cwd))


def get_diverged_files(dir_a: str | Path, dir_b: str | Path) -> tuple[list[str], list[str]]:
    """Compare uncommitted changes between two checkouts on the same branch.

    Returns (only_in_a, only_in_b): files that are dirty in one side
    but either clean or different in the other. Files that are dirty
    in both sides with identical content are excluded.
    """
    import filecmp

    a, b = Path(dir_a), Path(dir_b)
    dirty_a = set(get_changed_files("HEAD", cwd=dir_a))
    dirty_b = set(get_changed_files("HEAD", cwd=dir_b))

    only_a: list[str] = []
    only_b: list[str] = []

    all_files = dirty_a | dirty_b
    for f in sorted(all_files):
        fa, fb = a / f, b / f
        a_exists, b_exists = fa.is_file(), fb.is_file()

        if not a_exists and not b_exists:
            continue  # both deleted — identical state

        if a_exists and b_exists and filecmp.cmp(str(fa), str(fb), shallow=False):
            continue  # identical on both sides — not diverged

        if f in dirty_a:
            only_a.append(f)
        if f in dirty_b:
            only_b.append(f)

    return only_a, only_b


def sync_changes(ref: str, src_dir: str | Path, dst_dir: str | Path) -> list[str]:
    """Copy changed files from src_dir to dst_dir.

    Compares src_dir's working tree against ref to find changed files,
    then copies each one to dst_dir. This is more reliable than patching
    because it handles repeated applies correctly — the target always gets
    the current state of each changed file.

    Returns list of file paths synced.
    """
    import shutil

    src = Path(src_dir)
    dst = Path(dst_dir)
    changed = get_changed_files(ref, cwd=src_dir)

    synced: list[str] = []
    for filepath in changed:
        src_file = src / filepath
        dst_file = dst / filepath

        if src_file.is_file():
            dst_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src_file), str(dst_file))
            synced.append(filepath)
        elif dst_file.is_file():
            # File was deleted in source
            dst_file.unlink()
            synced.append(filepath)

    return synced


def get_commits_since(base_sha: str, cwd: str | Path | None = None) -> list[str]:
    """Get list of commit SHAs on current branch since base_sha (oldest first)."""
    result = run_git("log", "--format=%H", "--reverse", f"{base_sha}..HEAD", cwd=cwd)
    return [sha for sha in result.stdout.strip().splitlines() if sha.strip()]


def stash_create(cwd: str | Path | None = None) -> str | None:
    """Create a stash commit without modifying working tree or stash list.

    This is a read-only safety backup. Returns the stash SHA, or None
    if the working tree is clean.
    """
    result = run_git("stash", "create", cwd=cwd)
    sha = result.stdout.strip()
    return sha if sha else None


def clean_working_tree(cwd: str | Path | None = None) -> None:
    """Reset working tree to HEAD — discard all changes and untracked files."""
    run_git("checkout", ".", cwd=cwd)
    run_git("clean", "-fd", cwd=cwd)


def cherry_pick_to_worktree(commits: list[str], worktree_path: str | Path) -> int:
    """Cherry-pick commits onto a clean worktree. Returns count picked.

    The worktree MUST be clean before calling this — the caller is
    responsible for saving and restoring any uncommitted state.
    """
    if not commits:
        return 0
    for sha in commits:
        run_git("cherry-pick", sha, cwd=worktree_path)
    return len(commits)


def ensure_git_exclude(
    canonical_root: str | Path, extra_entries: list[str] | None = None
) -> None:
    """Add .flash/ (and any extra patterns) to .git/info/exclude."""
    exclude_file = Path(canonical_root) / ".git" / "info" / "exclude"
    exclude_file.parent.mkdir(parents=True, exist_ok=True)

    entries = [".flash/"]
    if extra_entries:
        entries.extend(extra_entries)

    content = exclude_file.read_text() if exclude_file.exists() else ""

    added = False
    for entry in entries:
        if entry not in content:
            if content and not content.endswith("\n"):
                content += "\n"
            content += entry + "\n"
            added = True

    if added:
        exclude_file.write_text(content)

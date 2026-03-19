"""State file read/write/clear for flash sessions."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

STATE_DIR = ".flash"
STATE_FILE = "state.json"


@dataclass
class FlashState:
    original_branch: str
    flash_branch: str
    temp_branch: str
    worktree_path: str
    canonical_root: str
    original_head_sha: str
    flash_base_sha: str
    started_at: str
    stash_sha: str | None = None


def state_dir(canonical_root: str | Path) -> Path:
    return Path(canonical_root) / STATE_DIR


def state_path(canonical_root: str | Path) -> Path:
    return state_dir(canonical_root) / STATE_FILE


def read_state(canonical_root: str | Path) -> FlashState | None:
    """Read the current flash state, or None if not flashed in."""
    path = state_path(canonical_root)
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    # Filter to known fields so old state files with removed fields still load
    known = {f.name for f in FlashState.__dataclass_fields__.values()}
    return FlashState(**{k: v for k, v in data.items() if k in known})


def write_state(state: FlashState) -> None:
    """Write flash state to disk."""
    directory = state_dir(state.canonical_root)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / STATE_FILE
    path.write_text(json.dumps(asdict(state), indent=2) + "\n")


def clear_state(canonical_root: str | Path) -> None:
    """Remove the .flash directory entirely."""
    import shutil

    directory = state_dir(canonical_root)
    if directory.exists():
        shutil.rmtree(directory)


def now_iso() -> str:
    return datetime.now(UTC).isoformat()

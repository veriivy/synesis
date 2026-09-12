"""Git via subprocess. Clone the demo target, commit as each agent.

One commit per agent per task, authored as that agent, so `git log` on the demo repo
shows the negotiation's outcome attributed to the agents that produced it.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

GIT_TIMEOUT = 120

# Windows dev machines frequently have git only inside GitHub Desktop, which does not
# put it on PATH. Checking these first turns a confusing "git is not recognized" at 2am
# into a working clone.
_FALLBACK_GIT_PATHS = [
    r"C:\Program Files\Git\cmd\git.exe",
    r"C:\Program Files (x86)\Git\cmd\git.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Git\cmd\git.exe"),
]


class GitError(RuntimeError):
    pass


def find_git() -> str:
    """Absolute path to a usable git, or raise with something actionable."""
    found = shutil.which("git")
    if found:
        return found

    for candidate in _FALLBACK_GIT_PATHS:
        if candidate and Path(candidate).is_file():
            log.warning("git is not on PATH; using %s", candidate)
            return candidate

    # GitHub Desktop bundles git under a versioned app directory.
    local = os.environ.get("LOCALAPPDATA")
    if local:
        desktop = Path(local) / "GitHubDesktop"
        if desktop.is_dir():
            matches = sorted(desktop.glob("app-*/resources/app/git/cmd/git.exe"))
            if matches:
                log.warning("git is not on PATH; using GitHub Desktop's copy: %s", matches[-1])
                return str(matches[-1])

    raise GitError(
        "git was not found. Install Git for Windows (https://git-scm.com/download/win) "
        "and reopen the terminal, or set GIT_EXECUTABLE to a git binary."
    )


def git_executable() -> str:
    return os.environ.get("GIT_EXECUTABLE") or find_git()


@dataclass
class CommitResult:
    sha: str
    files: list[str]
    message: str


def _run(args: list[str], cwd: Path | None = None, env: dict | None = None) -> str:
    cmd = [git_executable(), *args]
    log.debug("git %s (cwd=%s)", " ".join(args), cwd)
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
            env={**os.environ, **(env or {})},
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git {' '.join(args)} timed out after {GIT_TIMEOUT}s") from exc

    if proc.returncode != 0:
        raise GitError(
            f"git {' '.join(args)} failed ({proc.returncode}): "
            f"{proc.stderr.strip() or proc.stdout.strip()}"
        )
    return proc.stdout.strip()


def clone(url: str, dest: Path, ref: str = "main") -> str:
    """Clone the demo target into a fresh per-room workspace. Returns the HEAD sha.

    The destination is wiped first, so a demo can always be re-run from a clean repo —
    which is the whole reason the target repo is separate and never edited by hand.
    """
    dest = Path(dest)
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    dest.parent.mkdir(parents=True, exist_ok=True)

    _run(["clone", "--depth", "1", "--branch", ref, url, str(dest)])
    return _run(["rev-parse", "HEAD"], cwd=dest)


def init_local(dest: Path) -> str:
    """Initialise an empty repo. Used when TARGET_REPO_URL is unset, so the server
    still starts and the UI still works without network or a demo target."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    _run(["init", "-q", "-b", "main"], cwd=dest)
    (dest / "README.md").write_text("# scratch workspace\n", encoding="utf-8")
    _run(["add", "-A"], cwd=dest)
    _run(["-c", "user.name=synesis", "-c", "user.email=bot@synesis.local",
          "commit", "-q", "-m", "initial"], cwd=dest)
    return _run(["rev-parse", "HEAD"], cwd=dest)


def commit_as_agent(
    repo: Path, agent_id: str, message: str, files: list[str]
) -> CommitResult | None:
    """Stage exactly `files` and commit them authored as `agent_id`.

    Only the named paths are staged — never `git add -A` — so one agent's commit can
    never sweep up another agent's in-flight work.

    Returns None when there is nothing to commit, which is not an error: an agent that
    wrote no files simply produces no commit.
    """
    repo = Path(repo)
    existing = [f for f in files if (repo / f).exists()]
    if not existing:
        return None

    _run(["add", "--", *existing], cwd=repo)

    status = _run(["status", "--porcelain", "--", *existing], cwd=repo)
    if not status:
        return None

    identity = [
        "-c", f"user.name={agent_id} (synesis agent)",
        "-c", f"user.email={agent_id}@synesis.local",
    ]
    _run([*identity, "commit", "-q", "-m", message, "--", *existing], cwd=repo)
    sha = _run(["rev-parse", "HEAD"], cwd=repo)
    return CommitResult(sha=sha, files=existing, message=message)


def diff_for(repo: Path, path: str, base: str = "HEAD~1") -> str:
    """Unified diff of one file, for GET /rooms/{id}/files. Empty string on failure."""
    try:
        return _run(["diff", base, "HEAD", "--", path], cwd=Path(repo))
    except GitError:
        try:
            return _run(["show", f"HEAD:{path}"], cwd=Path(repo))
        except GitError:
            return ""

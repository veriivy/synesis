// Phase 1 stand-in for GET /rooms/{id}/files and /rooms/{id}/files/{path}.
// Paths match the ticket `files_owned` in /fixtures/tickets.json so the execution
// phase lights up the real files. `after` is the content the file takes once its
// owning agent lands an accepted write.

export interface WorkspaceFile {
  path: string;
  language: string;
  content: string;
  after?: string;
}

export const workspaceFiles: WorkspaceFile[] = [
  {
    path: "README.md",
    language: "markdown",
    content: `# ledger-api

Small expense-tracking API. Currently unauthenticated: every route is public
and \`current_user\` is hard-coded to the seed user.

## Layout

- \`src/app.py\` — routes
- \`src/auth/\` — authentication (empty; this is the work)
- \`src/models/user.py\` — the User record
- \`tests/\` — pytest

## Running

    pip install -r requirements.txt
    uvicorn src.app:app --reload
`,
  },
  {
    path: "requirements.txt",
    language: "text",
    content: `fastapi==0.115.0
uvicorn==0.30.6
pydantic==2.9.2
pytest==8.3.3
`,
  },
  {
    path: "src/app.py",
    language: "python",
    content: `from fastapi import FastAPI

from src.models.user import SEED_USER, User

app = FastAPI(title="ledger-api")

_EXPENSES: list[dict] = []


def current_user() -> User:
    # TODO: no authentication yet. Everything runs as the seed user.
    return SEED_USER


@app.get("/expenses")
def list_expenses() -> list[dict]:
    user = current_user()
    return [e for e in _EXPENSES if e["user_id"] == user.id]


@app.post("/expenses")
def create_expense(amount_cents: int, memo: str) -> dict:
    user = current_user()
    expense = {"user_id": user.id, "amount_cents": amount_cents, "memo": memo}
    _EXPENSES.append(expense)
    return expense
`,
  },
  {
    path: "src/auth/__init__.py",
    language: "python",
    content: "",
  },
  {
    path: "src/auth/session.py",
    language: "python",
    content: `"""Session helpers. Not written yet — ticket t1 (a1)."""
`,
    after: `"""HttpOnly cookie sessions. Owned by a1 (ticket t1)."""

import secrets
import time
from dataclasses import dataclass

COOKIE_NAME = "sid"
SESSION_TTL_SECONDS = 60 * 60 * 24 * 14


@dataclass(frozen=True)
class Session:
    sid: str
    user_id: str
    expires_at: float


_SESSIONS: dict[str, Session] = {}


def issue_session(user_id: str) -> Session:
    sid = secrets.token_urlsafe(32)
    session = Session(sid=sid, user_id=user_id, expires_at=time.time() + SESSION_TTL_SECONDS)
    _SESSIONS[sid] = session
    return session


def read_session(sid: str | None) -> Session | None:
    if not sid:
        return None
    session = _SESSIONS.get(sid)
    if session is None or session.expires_at < time.time():
        return None
    return session


def revoke_session(sid: str) -> None:
    _SESSIONS.pop(sid, None)
`,
  },
  {
    path: "src/auth/jwt.py",
    language: "python",
    content: `"""JWT helpers. Not written yet — ticket t2 (a2)."""
`,
    after: `"""HS256 bearer tokens, 15 minute expiry. Owned by a2 (ticket t2)."""

import base64
import hashlib
import hmac
import json
import os
import time

ALGORITHM = "HS256"
ACCESS_TTL_SECONDS = 15 * 60
_SECRET = os.environ["LEDGER_JWT_SECRET"].encode()


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _sign(signing_input: bytes) -> str:
    return _b64(hmac.new(_SECRET, signing_input, hashlib.sha256).digest())


def issue_access_token(user_id: str) -> str:
    header = _b64(json.dumps({"alg": ALGORITHM, "typ": "JWT"}).encode())
    payload = _b64(json.dumps({"sub": user_id, "exp": int(time.time()) + ACCESS_TTL_SECONDS}).encode())
    signing_input = f"{header}.{payload}".encode()
    return f"{header}.{payload}.{_sign(signing_input)}"


def verify_access_token(token: str) -> str | None:
    """Return the subject, or None. Exported for middleware.py (a1 owns that file)."""
    try:
        header, payload, signature = token.split(".")
    except ValueError:
        return None
    if not hmac.compare_digest(_sign(f"{header}.{payload}".encode()), signature):
        return None
    claims = json.loads(base64.urlsafe_b64decode(payload + "=="))
    if claims.get("exp", 0) < time.time():
        return None
    return claims.get("sub")
`,
  },
  {
    path: "src/auth/middleware.py",
    language: "python",
    content: `"""authenticate_user dispatcher. Not written yet — ticket t3 (a1)."""
`,
    after: `"""One gate, two mechanisms. Cookie first, then Bearer. Owned by a1 (ticket t3)."""

from src.auth.jwt import verify_access_token
from src.auth.session import COOKIE_NAME, read_session
from src.models.user import User, find_user


def authenticate_user(cookies: dict[str, str], headers: dict[str, str]) -> User | None:
    session = read_session(cookies.get(COOKIE_NAME))
    if session is not None:
        return find_user(session.user_id)

    authorization = headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        user_id = verify_access_token(authorization.split(" ", 1)[1])
        if user_id is not None:
            return find_user(user_id)

    return None
`,
  },
  {
    path: "src/models/user.py",
    language: "python",
    content: `from dataclasses import dataclass


@dataclass(frozen=True)
class User:
    id: str
    email: str
    password_hash: str


SEED_USER = User(id="u_seed", email="dev@example.com", password_hash="!")

_USERS: dict[str, User] = {SEED_USER.id: SEED_USER}


def find_user(user_id: str) -> User | None:
    return _USERS.get(user_id)


def find_user_by_email(email: str) -> User | None:
    return next((u for u in _USERS.values() if u.email == email), None)
`,
  },
  {
    path: "tests/test_auth.py",
    language: "python",
    content: `import pytest

pytestmark = pytest.mark.skip(reason="no authentication yet — ticket t4 (a2)")


def test_login_sets_cookie_and_returns_token():
    ...
`,
  },
];

/* ------------------------------ tree building ------------------------------- */

export interface FileNode {
  type: "file";
  name: string;
  path: string;
}

export interface DirNode {
  type: "dir";
  name: string;
  path: string;
  children: TreeNode[];
}

export type TreeNode = FileNode | DirNode;

/** Build a nested tree from a flat path list. Directories sort before files. */
export function buildTree(paths: string[]): TreeNode[] {
  const root: TreeNode[] = [];

  for (const path of paths) {
    const segments = path.split("/");
    let level = root;
    segments.forEach((name, i) => {
      const isLeaf = i === segments.length - 1;
      const here = segments.slice(0, i + 1).join("/");
      if (isLeaf) {
        level.push({ type: "file", name, path: here });
        return;
      }
      let dir = level.find((n): n is DirNode => n.type === "dir" && n.name === name);
      if (!dir) {
        dir = { type: "dir", name, path: here, children: [] };
        level.push(dir);
      }
      level = dir.children;
    });
  }

  const sort = (nodes: TreeNode[]): TreeNode[] =>
    nodes
      .map((n) => (n.type === "dir" ? { ...n, children: sort(n.children) } : n))
      .sort((a, b) => {
        if (a.type !== b.type) return a.type === "dir" ? -1 : 1;
        return a.name.localeCompare(b.name);
      });

  return sort(root);
}

"""The coding agent: the thing that makes execution write real code.

Until this module existed, execution.py wrote `# TODO: implement — ...`
placeholders: the scheduling and the write_file enforcement were real, the
code was not. This closes that gap. One model call per ticket, and every
path it comes back with is pushed through execution.write_file, so a model
that reaches for a file its ticket does not own gets refused by the runtime
rather than trusted.

Two deliberate choices, both about reliability:

*Not JSON.* Every other model call in this codebase asks for JSON and
repairs it (k2.extract_object). Source files inside JSON strings are a bad
trade: a single unescaped newline or quote loses the whole response, and a
truncated response loses every file rather than the last one. So the coder
answers in a line-delimited block format (`=== FILE: path ===`) that
parses incrementally — a truncated tail costs one file, not the ticket.
One retry on an unparseable response, then the placeholder fallback, which
is the same timeout/retry/fallback rule CLAUDE.md sets for every model call.

*The readable context is handed over up front,* rather than run as a
multi-turn read_file tool loop. read_file is a real capability either way
(execution.read_file, injected here as `read_file`); what the MVP skips is
letting the model spend three round trips discovering a nine-file repo it
can be shown in one. CLAUDE.md names latency as the number-one demo risk,
and a tool loop multiplies exactly the thing that is already slowest.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from .providers import agent_provider, chat
from .schemas import FinalPlan, Ticket

log = logging.getLogger(__name__)

#: How much of the repo the coder is shown. Files it owns are always included
#: in full; the rest fill the remaining budget.
MAX_CONTEXT_CHARS = int(os.getenv("CODING_CONTEXT_CHARS", "24000") or 24000)
MAX_TOKENS = int(os.getenv("CODING_MAX_TOKENS", "8192") or 8192)

ReadFile = Callable[[str], str | None]


@dataclass
class TicketCode:
    """What one coding attempt produced. `files` is path -> complete content.

    Paths are whatever the model returned, NOT filtered to files_owned —
    filtering here would hide an overreach that write_file is supposed to
    refuse out loud.
    """

    files: dict[str, str] = field(default_factory=dict)
    #: "agent" when a model wrote it, "placeholder" when we fell back.
    source: str = "agent"
    error: str | None = None


CODER_SYSTEM = """\
You are {agent_id}, a software engineer implementing ONE ticket in a repository that
other agents are editing at the same time as you.

YOU OWN THESE FILES. You may write these and nothing else:
{owned_block}

These files belong to other tickets right now. You may read them and import from
them. You may NOT write them:
{others_block}

RULES
1. Output the COMPLETE final content of every file you own. Not a diff, not a
   fragment, not an ellipsis.
2. Never write, rename or delete a file you do not own. The runtime rejects a write
   to a path outside your ticket — that refusal is enforced in code, not by asking.
3. Implement what this ticket says, consistent with the approved plan. Do not
   re-open decisions the plan already settled.
4. Match the repository's existing style, imports and Python version.
5. If you need something a file you do not own provides, import it under the name
   the plan gives it. Do not reimplement it in your own file.
6. Real, working code. No TODOs, no `pass` bodies, no "implementation omitted".

OUTPUT FORMAT. Emit nothing but these blocks — no prose, no markdown fences:
=== FILE: path/to/file.py ===
<complete file content>
=== END FILE ===
=== FILE: another/file.py ===
<complete file content>
=== END FILE ===
"""

_FILE_HEADER = re.compile(r"^\s*===\s*FILE:\s*(?P<path>.+?)\s*===\s*$")
_FILE_FOOTER = re.compile(r"^\s*===\s*END\s+FILE\s*===\s*$")
_FENCE = re.compile(r"^\s*```[\w.+-]*\s*$")


def parse_file_blocks(raw: str) -> dict[str, str]:
    """Parse `=== FILE: path ===` blocks. Tolerates a missing final footer
    (a truncated response), stray markdown fences the model added anyway,
    and text before the first header. Unterminated or empty blocks are
    dropped rather than written as an empty file."""
    files: dict[str, str] = {}
    path: str | None = None
    body: list[str] = []

    def flush() -> None:
        nonlocal path, body
        if path is not None:
            content = "\n".join(body)
            # Strip one layer of markdown fencing if the model added it.
            lines = content.split("\n")
            while lines and not lines[0].strip():
                lines.pop(0)
            while lines and not lines[-1].strip():
                lines.pop()
            if lines and _FENCE.match(lines[0]) and lines[-1].strip() == "```":
                lines = lines[1:-1]
            content = "\n".join(lines)
            if content.strip():
                files[path] = content.rstrip() + "\n"
        path, body = None, []

    for line in raw.splitlines():
        header = _FILE_HEADER.match(line)
        if header:
            flush()
            path = header.group("path").strip().strip("`").lstrip("./")
            continue
        if _FILE_FOOTER.match(line):
            flush()
            continue
        if path is not None:
            body.append(line)
    flush()
    if files:
        return files
    return _files_from_json(raw)


def _files_from_json(raw: str) -> dict[str, str]:
    """Gemini/K2 often ignore the block format and emit {"files": {...}}."""
    try:
        from .k2 import extract_object

        data = extract_object(raw)
    except ValueError:
        return {}
    blob = data.get("files") if isinstance(data, dict) else None
    if not isinstance(blob, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in blob.items():
        if isinstance(key, str) and isinstance(value, str) and value.strip():
            path = key.strip().strip("`").lstrip("./")
            out[path] = value.rstrip() + "\n"
    return out


def placeholder_content(ticket: Ticket, path: str) -> str:
    """The pre-coding-agent fallback. Used when no model can be reached, or
    when two attempts came back unusable: a ticket still has to produce
    something, and a stub that says so is more honest than half a file."""
    return (
        f'"""{ticket.title}. Owned by {ticket.assigned_agent} (ticket {ticket.ticket_id})."""\n\n'
        f"# TODO: implement — {ticket.description}\n"
        f"# Written as a placeholder: no coding agent output was available for {path}.\n"
    )


def placeholder_code(ticket: Ticket, error: str | None = None) -> TicketCode:
    return TicketCode(
        files={path: placeholder_content(ticket, path) for path in ticket.files_owned},
        source="placeholder",
        error=error,
    )


def build_repo_view(
    *,
    paths: Sequence[str],
    read_file: ReadFile,
    owned: Sequence[str],
    limit: int = MAX_CONTEXT_CHARS,
) -> str:
    """The repo as the coder sees it. Owned files first and always; the rest
    until the character budget runs out, so a big repo degrades into "the
    files this ticket is about" instead of a truncated prompt."""
    ordered = list(owned) + [p for p in sorted(paths) if p not in set(owned)]
    chunks: list[str] = []
    used = 0
    skipped: list[str] = []

    for path in ordered:
        content = read_file(path)
        if content is None:
            continue
        block = f"--- {path} ---\n{content}\n"
        if path not in set(owned) and used + len(block) > limit:
            skipped.append(path)
            continue
        chunks.append(block)
        used += len(block)

    if skipped:
        chunks.append(f"--- (not shown, ask if needed: {', '.join(skipped)}) ---\n")
    return "".join(chunks)


def _ownership_blocks(ticket: Ticket, all_tickets: Sequence[Ticket]) -> tuple[str, str]:
    owned = "\n".join(f"- {p}" for p in ticket.files_owned) or "- (none)"
    others = [
        f"- {p}  (ticket {t.ticket_id}, agent {t.assigned_agent})"
        for t in all_tickets
        if t.ticket_id != ticket.ticket_id
        for p in t.files_owned
    ]
    return owned, "\n".join(others) or "- (none)"


def _user_prompt(
    *,
    ticket: Ticket,
    plan: FinalPlan | None,
    context: str | None,
    repo_view: str,
) -> str:
    steps = []
    for step in plan.steps if plan else []:
        touches = set(step.files_touched) & set(ticket.files_owned)
        marker = "  <-- your files" if touches else ""
        steps.append(f"- {step.title}: {step.description} {list(step.files_touched)}{marker}")

    return (
        f"TICKET {ticket.ticket_id}: {ticket.title}\n"
        f"{ticket.description}\n\n"
        f"APPROVED PLAN\n{plan.summary if plan else '(none)'}\n"
        + ("\n".join(steps) + "\n" if steps else "")
        + (f"\nSHARED CONTEXT\n{context}\n" if context else "")
        + f"\nTHE REPOSITORY AS IT STANDS\n{repo_view}\n"
        f"\nWrite the complete final content of every file you own"
        f" ({', '.join(ticket.files_owned) or 'none'}). Blocks only."
    )


def implement_ticket_sync(
    *,
    ticket: Ticket,
    all_tickets: Sequence[Ticket],
    plan: FinalPlan | None,
    context: str | None,
    read_file: ReadFile,
    paths: Sequence[str],
    provider: str | None = None,
    api_key: str | None = None,
    chat_fn: Callable[..., str] | None = None,
) -> TicketCode:
    """One ticket's worth of real code. Blocking — call via asyncio.to_thread.

    Timeout and one transport retry live in providers.chat; the retry here
    is for an unparseable response, and the fallback after that is the
    placeholder stub. A failure never propagates: a ticket that cannot be
    coded still executes and still reports honestly.
    """
    send = chat_fn or chat
    owned_block, others_block = _ownership_blocks(ticket, all_tickets)
    system = CODER_SYSTEM.format(
        agent_id=ticket.assigned_agent, owned_block=owned_block, others_block=others_block
    )
    user = _user_prompt(
        ticket=ticket,
        plan=plan,
        context=context,
        repo_view=build_repo_view(paths=paths, read_file=read_file, owned=ticket.files_owned),
    )

    last_error: str | None = None
    for attempt in (1, 2):
        try:
            raw = send(
                system=system,
                user=user if attempt == 1 else user + "\n\nYour last reply could not be"
                " parsed. Emit ONLY `=== FILE: path ===` / `=== END FILE ===` blocks.",
                provider=provider or agent_provider(ticket.assigned_agent),
                max_tokens=MAX_TOKENS,
                api_key=api_key,
            )
        except Exception as exc:  # noqa: BLE001 — no key, no network, provider down
            log.warning("coding call failed for %s: %s", ticket.ticket_id, exc)
            return placeholder_code(ticket, error=str(exc))

        files = parse_file_blocks(raw)
        if files:
            return TicketCode(files=files, source="agent")
        last_error = "model returned no parseable file blocks"
        log.warning("%s: %s (attempt %d)", ticket.ticket_id, last_error, attempt)

    return placeholder_code(ticket, error=last_error)


async def implement_ticket(**kwargs) -> TicketCode:
    """asyncio wrapper, so two tickets in the same lane really do code in
    parallel instead of queueing behind one blocking HTTP call."""
    return await asyncio.to_thread(lambda: implement_ticket_sync(**kwargs))

"""Engine CLI. Run the negotiation without the server, and confirm model ids.

    python -m engine.cli doctor              # which keys are set, which models resolve
    python -m engine.cli models anthropic    # model ids this key can actually reach
    python -m engine.cli negotiate           # full negotiation from fixtures/intents.json

`doctor` before every demo. `models` is the answer to "confirm exact model IDs at
runtime, don't assume" — an invalid id in .env fails here in two seconds instead of on
stage.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from .config import PROVIDERS, Settings, api_key_for, model_for
from .negotiation import Negotiation
from .providers import ProviderError, build_client
from .schemas import AgentSpec, RepoRef, Requirement

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


async def cmd_doctor(_: argparse.Namespace) -> int:
    settings = Settings.from_env()
    print(f"enabled agents : {', '.join(settings.enabled_agents)}")
    print(f"moderator      : {settings.moderator_provider}")
    print(f"max rounds     : {settings.max_rounds}\n")

    failures = 0
    for key, cfg in PROVIDERS.items():
        marker = "*" if key in settings.enabled_agents else " "
        if not api_key_for(key):
            print(f"{marker} {key:<10} no key ({cfg.api_key_env} unset)")
            if key in settings.enabled_agents:
                failures += 1
            continue

        model = model_for(key)
        try:
            client = build_client(key, settings)
            available = await client.list_models()
        except (ProviderError, Exception) as exc:  # noqa: BLE001 — report, never crash
            print(f"{marker} {key:<10} key set, but list_models failed: {exc}")
            if key in settings.enabled_agents:
                failures += 1
            continue

        if available and model not in available:
            near = [m for m in available if model.split("-")[0] in m][:5]
            print(f"{marker} {key:<10} MODEL NOT FOUND: {model!r}")
            print(f"{'':12} try: {', '.join(near) or ', '.join(available[:5])}")
            if key in settings.enabled_agents:
                failures += 1
        else:
            print(f"{marker} {key:<10} ok  model={model}  ({len(available)} available)")

    if failures:
        print(f"\n{failures} enabled agent(s) will not run. Fix .env before demoing.")
    return 1 if failures else 0


async def cmd_models(args: argparse.Namespace) -> int:
    try:
        client = build_client(args.provider)
        for model_id in sorted(await client.list_models()):
            print(model_id)
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def _load_specs(path: Path) -> tuple[list[AgentSpec], str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    specs = [
        AgentSpec(
            agent_id=p["agent_id"],
            provider=p["provider"],
            model=model_for(p["provider"]),
            user_id=p["user_id"],
            display_name=p.get("display_name", ""),
            requirements=[Requirement(**r) for r in p.get("requirements", [])],
        )
        for p in data["participants"]
    ]
    return specs, data["feature"]


async def cmd_negotiate(args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    specs, feature = _load_specs(Path(args.intents))

    negotiation = Negotiation(
        room_id="cli",
        specs=specs,
        feature=feature,
        repo_map=Path(args.repo_map).read_text(encoding="utf-8") if args.repo_map else "",
        repo=RepoRef(),
    )

    async for event in negotiation.run():
        if args.jsonl:
            print(event.model_dump_json())
            continue
        if event.type == "agent_message":
            print(f"\n=== round {event.round} · {event.agent_id} ===\n{event.content}")
        elif event.type == "round_complete":
            print(f"\n--- round {event.round} complete ---")
        elif event.type == "plan_proposed":
            print("\n=== PLAN ===")
            print(event.plan.model_dump_json(indent=2))
        elif event.type == "deadlock":
            print("\n=== DEADLOCK ===")
            for issue in event.unresolved:
                print(f"  {issue.issue}")
                for agent, pos in issue.positions.items():
                    print(f"    {agent}: {pos}")

    return 0 if negotiation.result.succeeded else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="engine", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="check keys and model ids").set_defaults(func=cmd_doctor)

    models = sub.add_parser("models", help="list model ids a provider key can reach")
    models.add_argument("provider", choices=sorted(PROVIDERS))
    models.set_defaults(func=cmd_models)

    negotiate = sub.add_parser("negotiate", help="run a negotiation from an intents file")
    negotiate.add_argument("--intents", default=str(FIXTURES / "intents.json"))
    negotiate.add_argument("--repo-map", default=None, help="path to a repo map text file")
    negotiate.add_argument("--jsonl", action="store_true", help="emit raw contract events")
    negotiate.set_defaults(func=cmd_negotiate)

    args = parser.parse_args(argv)
    return asyncio.run(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())

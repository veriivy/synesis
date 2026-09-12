"""CLI: run one K2 analysis on the fixture PoAs and print JSON."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from orchestrator.k2 import analyze, load_fixture

ROOT = Path(__file__).resolve().parent


def main() -> int:
    load_dotenv(ROOT / ".env")
    analysis = analyze(
        context=load_fixture("context.json"),
        tasks=load_fixture("tasks.json"),
        poa1=load_fixture("PoA1.json"),
        poa2=load_fixture("PoA2.json"),
        round_index=1,
    )
    print(json.dumps(analysis, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

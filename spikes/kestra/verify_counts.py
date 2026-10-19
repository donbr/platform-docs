"""Promotion gate: exit 1 unless the POC collection holds >= expected docs."""
import argparse
import json
import os
import sys
from pathlib import Path

from qdrant_client import QdrantClient

from spikes.kestra import poc_config

BASELINES_PATH = Path(__file__).with_name("source_baselines.json")


def is_complete(actual: int, expected: int, exact: bool = False) -> bool:
    return actual == expected if exact else actual >= expected


def load_baselines(path: Path = BASELINES_PATH) -> dict[str, int]:
    """Read the committed per-source page floors."""
    return json.loads(path.read_text())["floors"]


def check_source_floors(pages_dir: Path, baselines: dict[str, int]) -> list[tuple[str, int, int]]:
    """Return (source, actual, floor) for every source below its floor.

    The corpus total alone cannot catch a single source collapsing — Zep fell from
    ~196 pages to 1 and the total still looked healthy enough to promote twice.
    """
    shortfalls = []
    for source, floor in sorted(baselines.items()):
        source_dir = pages_dir / source
        actual = (
            sum(1 for f in source_dir.glob("*.json") if f.name != "manifest.json")
            if source_dir.is_dir() else 0
        )
        if actual < floor:
            shortfalls.append((source, actual, floor))
    return shortfalls


def resolve_expected(expected_arg: int) -> int:
    """A positive --expected overrides (used for the failure test); otherwise
    compute the real expectation from the split output (number of page files)."""
    if expected_arg and expected_arg > 0:
        return expected_arg
    return poc_config.expected_doc_count(poc_config.POC_SOURCES)


def qdrant_count(collection: str) -> int:
    client = QdrantClient(url=os.environ["QDRANT_API_URL"], api_key=os.environ["QDRANT_API_KEY"])
    return client.count(collection_name=collection, exact=True).count


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--collection", required=True)
    p.add_argument("--expected", type=int, default=0,
                   help="positive value overrides; 0 (default) computes from split output")
    p.add_argument("--exact", action="store_true",
                   help="require actual == expected (surplus means duplicates)")
    p.add_argument("--check-floors", action="store_true",
                   help="also fail if any source is below its source_baselines.json floor")
    args = p.parse_args()
    expected = resolve_expected(args.expected)
    args.expected = expected  # for the print below
    actual = qdrant_count(args.collection)
    ok = is_complete(actual, args.expected, exact=args.exact)
    print(f"verify_counts: collection={args.collection} actual={actual} expected={args.expected} ok={ok}")

    shortfalls = (
        check_source_floors(poc_config.DEFAULT_PAGES_DIR, load_baselines())
        if args.check_floors else []
    )
    for source, source_actual, floor in shortfalls:
        print(f"verify_counts: SOURCE BELOW FLOOR {source} actual={source_actual} floor={floor}")

    sys.exit(0 if (ok and not shortfalls) else 1)


if __name__ == "__main__":
    main()

"""Emit an expected doc count as a Kestra output var.

Kestra's scripts plugin captures a ``::{"outputs": {...}}::`` line from stdout
into the task's outputs, so downstream tasks can reference
``{{ outputs.<task>.vars.count }}``.

Default counts the POC sources; ``--all`` counts every split source (used by the
production refresh, where the whole corpus is uploaded).
"""
import argparse
import json
from pathlib import Path

from spikes.kestra import poc_config


def kestra_output_line(count: int) -> str:
    return "::" + json.dumps({"outputs": {"count": count}}) + "::"


def count_all_sources(pages_dir: Path = poc_config.DEFAULT_PAGES_DIR) -> int:
    """Total split page files across every source directory (excludes manifests)."""
    if not pages_dir.is_dir():
        return 0
    total = 0
    for d in pages_dir.iterdir():
        if d.is_dir():
            total += sum(1 for f in d.glob("*.json") if f.name != "manifest.json")
    return total


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--all", action="store_true", help="count every split source (full corpus), not just POC sources")
    args = p.parse_args()
    n = count_all_sources() if args.all else poc_config.expected_doc_count(poc_config.POC_SOURCES)
    print(f"expected_doc_count={n}")
    print(kestra_output_line(n))


if __name__ == "__main__":
    main()

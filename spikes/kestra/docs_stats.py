"""Gather current documentation-corpus stats and emit a CSV for a Google Sheet.

Rows (one per source): source, source_url, doc_count, last_downloaded,
collection_version, generated_at. Counts come from the LIVE production
collection (via the `platform-docs` alias); download date + source URLs from the
ETL manifest/config. The pure ``build_rows`` is unit-tested; ``main`` gathers
live data and writes the CSV that Kestra's ``sheets.Load`` task upserts.
"""
import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from qdrant_client import QdrantClient

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
from download_llms_raw import SOURCES  # noqa: E402

PROD_ALIAS = "platform-docs"
FIELDS = ["source", "source_url", "doc_count", "last_downloaded", "collection_version", "generated_at"]


def source_base_urls() -> dict[str, str]:
    """Map each source to its docs-site base URL (scheme://host) from SOURCES."""
    out = {}
    for name, (_llms, full) in SOURCES.items():
        p = urlparse(full)
        out[name] = f"{p.scheme}://{p.netloc}"
    return out


def build_rows(sources: dict[str, str], counts: dict[str, int],
               last_downloaded: str, collection_version: str, generated_at: str) -> list[dict]:
    """Pure: assemble one row per source. Sources with 0 live docs are included."""
    return [
        {
            "source": name,
            "source_url": sources[name],
            "doc_count": counts.get(name, 0),
            "last_downloaded": last_downloaded,
            "collection_version": collection_version,
            "generated_at": generated_at,
        }
        for name in sorted(sources)
    ]


def _client() -> QdrantClient:
    return QdrantClient(url=os.environ["QDRANT_API_URL"], api_key=os.environ["QDRANT_API_KEY"])


def live_counts(client: QdrantClient, collection: str) -> dict[str, int]:
    """Per-source doc counts from the live collection (scroll + tally source_name)."""
    counts: dict[str, int] = {}
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=collection, with_payload=["metadata.source_name"],
            with_vectors=False, limit=1000, offset=offset,
        )
        for p in points:
            sn = (p.payload or {}).get("metadata", {}).get("source_name")
            if sn:
                counts[sn] = counts.get(sn, 0) + 1
        if offset is None:
            break
    return counts


def alias_target(client: QdrantClient, alias: str) -> str:
    for a in client.get_aliases().aliases:
        if a.alias_name == alias:
            return a.collection_name
    return alias


def to_csv(rows: list[dict], path: str) -> None:
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True)
    args = p.parse_args()

    manifest = json.loads((REPO / "data" / "raw" / "manifest.json").read_text())
    last_downloaded = manifest.get("created_at", "")

    client = _client()
    target = alias_target(client, PROD_ALIAS)
    version = target[len("platform-docs-"):] if target.startswith("platform-docs-") else target
    counts = live_counts(client, PROD_ALIAS)
    generated_at = datetime.now(timezone.utc).isoformat()

    rows = build_rows(source_base_urls(), counts, last_downloaded, version, generated_at)
    to_csv(rows, args.out)
    print(f"docs_stats: {len(rows)} sources -> {args.out} (collection {target})")
    for r in rows:
        print(f"  {r['source']:14} {r['doc_count']:>5}  {r['source_url']}")


if __name__ == "__main__":
    main()

"""Generate a run summary report (markdown) for upload to Google Drive.

Qdrant-driven and DB-free: reads the POC collection counts and current sandbox
alias targets from Qdrant, and takes run metadata (id/status/expected) as args.
The pure ``render_report`` function is unit-tested; ``main`` gathers live Qdrant
state and writes the file that the Kestra Drive task uploads.
"""
import argparse
import os

from qdrant_client import QdrantClient

from spikes.kestra import poc_config


def render_report(run_id: str, status: str, expected: int,
                  counts: dict[str, int], aliases: dict[str, str]) -> str:
    """Render a markdown summary. Pure — no I/O — so it is unit-testable."""
    lines = [
        f"# platform-docs POC run summary",
        "",
        f"- **Run ID:** {run_id}",
        f"- **Status:** {status}",
        f"- **Docs expected (per collection):** {expected}",
        "",
        "## Collections",
        "",
        "| Collection | Points | Complete |",
        "|---|---:|:--:|",
    ]
    for coll, n in counts.items():
        complete = "✅" if n >= expected else "⚠️"
        lines.append(f"| {coll} | {n} | {complete} |")
    lines += ["", "## Sandbox aliases", "", "| Alias | → Collection |", "|---|---|"]
    for alias, target in aliases.items():
        lines.append(f"| {alias} | {target or '(unset)'} |")
    lines.append("")
    return "\n".join(lines)


def _client() -> QdrantClient:
    return QdrantClient(url=os.environ["QDRANT_API_URL"], api_key=os.environ["QDRANT_API_KEY"])


def gather_counts(client: QdrantClient) -> dict[str, int]:
    collections = [poc_config.POC_COLLECTION, poc_config.POC_COLLECTION_FASTEMBED]
    return {c: client.count(collection_name=c, exact=True).count for c in collections}


def gather_alias_targets(client: QdrantClient) -> dict[str, str]:
    wanted = {poc_config.POC_ALIAS, poc_config.POC_ALIAS_FASTEMBED}
    targets = {a: "" for a in wanted}
    for a in client.get_aliases().aliases:
        if a.alias_name in wanted:
            targets[a.alias_name] = a.collection_name
    return targets


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--run-id", required=True)
    p.add_argument("--status", default="success")
    p.add_argument("--expected", type=int, required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args()

    client = _client()
    md = render_report(args.run_id, args.status, args.expected,
                       gather_counts(client), gather_alias_targets(client))
    with open(args.out, "w") as fh:
        fh.write(md)
    print(f"report: wrote {args.out} ({len(md)} chars)")


if __name__ == "__main__":
    main()

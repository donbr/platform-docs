"""Prefect 3 equivalent of the Kestra spike flow — for a head-to-head comparison.

Same pipeline (download -> split -> upload x2 -> verify gate -> promote), same POC
collections/aliases, same Qdrant-side helpers (reused from spikes.kestra), so the
only variable is the orchestrator. Run state/telemetry is Prefect's own (Postgres
backend) — no hand-rolled pipeline_runs table needed, which is itself a difference
worth noting vs Kestra's JDBC-Query tasks.

Production safety: writes to POC collections only; alias_swap refuses prod aliases.
"""
import argparse
import os
import subprocess
import sys

from dotenv import load_dotenv
from prefect import flow, get_run_logger, task

# repo root on sys.path so we can reuse the Kestra spike's generic helpers
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
load_dotenv(os.path.join(REPO, ".env"))  # QDRANT_/OPENAI_ keys for in-process + subprocess

from spikes.kestra import alias_swap, poc_config, verify_counts  # noqa: E402


def _run(script_args: list[str]) -> None:
    subprocess.run(["uv", "run", *script_args], cwd=REPO, check=True)


@task(retries=3, retry_delay_seconds=30)
def download() -> None:
    _run(["scripts/download_llms_raw.py"])


@task
def split() -> None:
    _run(["scripts/split_llms_pages.py"])


@task(retries=3, retry_delay_seconds=60)
def upload(collection: str, script: str) -> None:
    _run([f"scripts/{script}", "--sources", "OpenAI", "Vue", "Supabase",
          "--collection", collection, "--batch-size", "25", "--workers", "2"])


@task
def verify(collection: str, expected_arg: int) -> int:
    """The promotion gate. Raises (fails the flow) on a shortfall so the
    downstream promote() tasks never run."""
    expected = verify_counts.resolve_expected(expected_arg)
    actual = verify_counts.qdrant_count(collection)
    get_run_logger().info(f"verify {collection}: actual={actual} expected={expected}")
    if not verify_counts.is_complete(actual, expected):
        raise ValueError(f"promotion gate FAILED: {collection} has {actual} < {expected}")
    return actual


@task
def promote(alias: str, collection: str) -> None:
    alias_swap.swap(alias, collection)  # guarded: refuses non-sandbox aliases


@flow(name="platform-docs-poc")
def poc(expected_doc_count: int = 0) -> None:
    download()
    split()
    upload(poc_config.POC_COLLECTION, "upload_to_qdrant.py")
    upload(poc_config.POC_COLLECTION_FASTEMBED, "upload_to_qdrant_fastembed.py")
    # gate — if either verify raises, the promote() calls below never execute
    verify(poc_config.POC_COLLECTION, expected_doc_count)
    verify(poc_config.POC_COLLECTION_FASTEMBED, expected_doc_count)
    promote(poc_config.POC_ALIAS, poc_config.POC_COLLECTION)
    promote(poc_config.POC_ALIAS_FASTEMBED, poc_config.POC_COLLECTION_FASTEMBED)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--expected", type=int, default=0,
                   help="0 = dynamic from split output; positive overrides (999+ to force gate failure)")
    args = p.parse_args()
    poc(expected_doc_count=args.expected)

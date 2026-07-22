"""Promotion gate: exit 1 unless the POC collection holds >= expected docs."""
import argparse
import os
import sys

from qdrant_client import QdrantClient

from spikes.kestra import poc_config


def is_complete(actual: int, expected: int) -> bool:
    return actual >= expected


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
    args = p.parse_args()
    expected = resolve_expected(args.expected)
    args.expected = expected  # for the print below
    actual = qdrant_count(args.collection)
    ok = is_complete(actual, args.expected)
    print(f"verify_counts: collection={args.collection} actual={actual} expected={args.expected} ok={ok}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

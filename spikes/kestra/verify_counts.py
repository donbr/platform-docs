"""Promotion gate: exit 1 unless the POC collection holds >= expected docs."""
import argparse
import os
import sys

from qdrant_client import QdrantClient


def is_complete(actual: int, expected: int) -> bool:
    return actual >= expected


def qdrant_count(collection: str) -> int:
    client = QdrantClient(url=os.environ["QDRANT_API_URL"], api_key=os.environ["QDRANT_API_KEY"])
    return client.count(collection_name=collection, exact=True).count


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--collection", required=True)
    p.add_argument("--expected", type=int, required=True)
    args = p.parse_args()
    actual = qdrant_count(args.collection)
    ok = is_complete(actual, args.expected)
    print(f"verify_counts: collection={args.collection} actual={actual} expected={args.expected} ok={ok}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

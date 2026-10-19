"""Delete a not-yet-live target collection so a non-idempotent upload starts clean.

Guard: Qdrant drops a collection's aliases when the collection is deleted, so
resetting a collection that a live alias points to takes production search down.
This refuses (non-zero exit) when ANY alias points at the collection.
"""
import argparse
import os
import sys

from qdrant_client import QdrantClient


def aliases_pointing_at(collection: str, aliases) -> list[str]:
    """Sorted names of aliases (objects with .alias_name/.collection_name) targeting ``collection``."""
    return sorted(a.alias_name for a in aliases if a.collection_name == collection)


def assert_safe_to_reset(collection: str, aliases) -> None:
    live = aliases_pointing_at(collection, aliases)
    if live:
        raise ValueError(
            f"refusing to reset {collection!r}: live alias(es) point to it: {live}. "
            "Use a NEW target_collection version."
        )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--collection", required=True)
    args = p.parse_args()
    client = QdrantClient(url=os.environ["QDRANT_API_URL"], api_key=os.environ["QDRANT_API_KEY"])
    try:
        assert_safe_to_reset(args.collection, client.get_aliases().aliases)
    except ValueError as e:
        print(f"reset_target: {e}", file=sys.stderr)
        sys.exit(1)
    if client.collection_exists(args.collection):
        client.delete_collection(args.collection)
    print(f"reset {args.collection}")


if __name__ == "__main__":
    main()

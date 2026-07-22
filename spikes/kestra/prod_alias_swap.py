"""Re-point a PRODUCTION alias to a freshly-built, verified collection.

This is the deliberate counterpart to ``alias_swap.py`` (which *refuses* production
aliases). It is heavily guarded: the alias must be a known production alias, the
caller must pass ``--confirm true``, and — belt and suspenders — the target
collection is re-counted against the expected total and the swap is refused on any
shortfall. Intended to run only after the flow's verify gate has already passed.
"""
import argparse
import os

from qdrant_client import QdrantClient
from qdrant_client.models import CreateAlias, CreateAliasOperation

from spikes.kestra import poc_config


def assert_production_swap_allowed(alias: str, confirm: bool) -> None:
    if alias not in poc_config.PROD_ALIASES:
        raise ValueError(f"{alias!r} is not a known production alias: {sorted(poc_config.PROD_ALIASES)}")
    if not confirm:
        raise ValueError("refusing to swap a production alias without --confirm true")


def swap_production(alias: str, collection: str, expected: int, confirm: bool) -> int:
    assert_production_swap_allowed(alias, confirm)
    client = QdrantClient(url=os.environ["QDRANT_API_URL"], api_key=os.environ["QDRANT_API_KEY"])
    actual = client.count(collection_name=collection, exact=True).count
    if actual < expected:
        raise ValueError(f"refusing swap: {collection} has {actual} < expected {expected}")
    client.update_collection_aliases(change_aliases_operations=[
        CreateAliasOperation(create_alias=CreateAlias(collection_name=collection, alias_name=alias)),
    ])
    print(f"prod_alias_swap: {alias} -> {collection} (count {actual} >= expected {expected})")
    return actual


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--alias", required=True)
    p.add_argument("--collection", required=True)
    p.add_argument("--expected", type=int, required=True)
    p.add_argument("--confirm", default="false", help="must be the string 'true' to actually swap")
    args = p.parse_args()
    swap_production(args.alias, args.collection, args.expected, args.confirm.lower() == "true")


if __name__ == "__main__":
    main()

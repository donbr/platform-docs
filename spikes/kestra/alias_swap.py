"""Point a SANDBOX alias at a POC collection. Refuses to touch production aliases."""
import argparse
import os

from qdrant_client import QdrantClient
from qdrant_client.models import CreateAlias, CreateAliasOperation

from spikes.kestra import poc_config


def assert_sandbox_alias(name: str) -> None:
    if name in poc_config.PROD_ALIASES or not name.endswith("-poc-active"):
        raise ValueError(f"refusing to swap non-sandbox alias: {name!r}")


def swap(alias: str, collection: str) -> None:
    assert_sandbox_alias(alias)
    client = QdrantClient(url=os.environ["QDRANT_API_URL"], api_key=os.environ["QDRANT_API_KEY"])
    client.update_collection_aliases(change_aliases_operations=[
        CreateAliasOperation(create_alias=CreateAlias(collection_name=collection, alias_name=alias)),
    ])
    print(f"alias_swap: {alias} -> {collection}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--alias", default=poc_config.POC_ALIAS)
    p.add_argument("--collection", default=poc_config.POC_COLLECTION)
    args = p.parse_args()
    swap(args.alias, args.collection)


if __name__ == "__main__":
    main()

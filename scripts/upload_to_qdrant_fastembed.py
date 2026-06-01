#!/usr/bin/env python3
"""
Upload documentation pages to Qdrant Cloud using FastEmbed embeddings.

This script loads JSON documents from data/interim/pages/, converts them to
LangChain Documents with metadata, and uploads them to Qdrant Cloud using
the free FastEmbed model (BAAI/bge-small-en-v1.5).

No OpenAI API key required!
"""

import argparse
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv
from langchain_core.documents import Document
from langchain_community.embeddings import FastEmbedEmbeddings
from langchain_qdrant import QdrantVectorStore
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams
from tqdm import tqdm

# Load environment variables
load_dotenv()

# Constants
COLLECTION_NAME = "platform-docs-fastembed"
# FastEmbed default model: BAAI/bge-small-en-v1.5 produces 384-dimensional vectors
EMBEDDING_DIMENSION = 384
BATCH_SIZE = 100
MAX_WORKERS = 4
DATA_DIR = Path(__file__).parent.parent / "data"
PAGES_DIR = DATA_DIR / "interim" / "pages"
PROCESSED_DIR = DATA_DIR / "processed"


def init_qdrant_client() -> QdrantClient:
    """Initialize Qdrant client with credentials from environment."""
    api_url = os.getenv("QDRANT_API_URL")
    api_key = os.getenv("QDRANT_API_KEY")

    if not api_url or not api_key:
        raise ValueError(
            "QDRANT_API_URL and QDRANT_API_KEY must be set in environment variables."
        )

    return QdrantClient(
        url=api_url,
        api_key=api_key,
        prefer_grpc=True,
    )


def get_fastembed_embeddings() -> FastEmbedEmbeddings:
    """Initialize FastEmbed embeddings (free, no API key needed)."""
    return FastEmbedEmbeddings(
        model_name="BAAI/bge-small-en-v1.5",
        # cache_dir can be set if needed
    )


def create_collection(client: QdrantClient, collection_name: str) -> None:
    """Create Qdrant collection if it doesn't exist."""
    if client.collection_exists(collection_name):
        print(f"✓ Collection '{collection_name}' already exists")
        return

    print(f"Creating collection '{collection_name}'...")
    client.create_collection(
        collection_name=collection_name,
        vectors_config=VectorParams(
            size=EMBEDDING_DIMENSION,
            distance=Distance.COSINE,
            on_disk=True,
        ),
        optimizers_config={
            "indexing_threshold": 10000,
        },
        on_disk_payload=True,
    )
    print(f"✓ Collection '{collection_name}' created successfully")


def load_documents(
    pages_dir: Path,
    source_filter: Optional[List[str]] = None,
) -> List[Document]:
    """Load all JSON files and convert to LangChain Documents."""
    manifest_path = pages_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")

    with open(manifest_path, "r") as f:
        manifest = json.load(f)

    documents = []
    sources_processed = []

    print(f"\nLoading documents from {pages_dir}...")

    for source_dir in sorted(pages_dir.iterdir()):
        if not source_dir.is_dir():
            continue

        source_name = source_dir.name
        if source_filter and source_name not in source_filter:
            print(f"  Skipping {source_name} (not in filter)")
            continue

        source_manifest = manifest["results"].get(source_name, {})
        if not source_manifest:
            print(f"  Warning: {source_name} not found in manifest")
            continue

        json_files = [f for f in source_dir.glob("*.json") if f.name != "manifest.json"]
        print(f"  Loading {len(json_files)} documents from {source_name}...")

        for json_file in json_files:
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            page_num = json_file.stem.split("_")[0]

            doc = Document(
                page_content=data["content"],
                metadata={
                    "title": data.get("title") or "Untitled",
                    "source_url": data.get("source_url"),
                    "content_length": data["content_length"],
                    "header_level": data.get("header_level"),
                    "section_path": data.get("section_path"),
                    "parent_title": data.get("parent_title"),
                    "parent_index": data.get("parent_index"),
                    "source_name": source_name,
                    "total_pages": source_manifest.get("page_count"),
                    "avg_content_length": source_manifest.get("avg_size_chars"),
                    "doc_id": f"{source_name}_{page_num}",
                    "page_number": page_num,
                },
            )
            documents.append(doc)

        sources_processed.append(source_name)

    print(f"\n✓ Loaded {len(documents)} documents from {len(sources_processed)} sources:")
    for source in sources_processed:
        source_docs = [d for d in documents if d.metadata["source_name"] == source]
        print(f"  - {source}: {len(source_docs)} documents")

    return documents


def upload_batch(
    batch: List[Document],
    vector_store: QdrantVectorStore,
    batch_idx: int,
) -> List[str]:
    """Upload a single batch of documents."""
    try:
        ids = vector_store.add_documents(documents=batch)
        return ids
    except Exception as e:
        print(f"\n✗ Batch {batch_idx} failed: {e}")
        raise


def upload_documents(
    documents: List[Document],
    embeddings,
    client: QdrantClient,
    collection_name: str,
    batch_size: int = BATCH_SIZE,
    max_workers: int = MAX_WORKERS,
    dry_run: bool = False,
) -> List[str]:
    """Upload documents in batches."""
    if dry_run:
        print(f"\n[DRY RUN] Would upload {len(documents)} documents to '{collection_name}'")
        return []

    vector_store = QdrantVectorStore(
        client=client,
        collection_name=collection_name,
        embedding=embeddings,
    )

    batches = [
        documents[i : i + batch_size] for i in range(0, len(documents), batch_size)
    ]

    print(f"\nUploading {len(documents)} documents in {len(batches)} batches...")
    print(f"Batch size: {batch_size}, Parallel workers: {max_workers}")
    print("Note: First batch may be slow as FastEmbed downloads the model")

    uploaded_ids = []
    failed_batches = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(upload_batch, batch, vector_store, idx): idx
            for idx, batch in enumerate(batches)
        }

        with tqdm(total=len(documents), desc="Uploading", unit="docs") as pbar:
            for future in as_completed(futures):
                batch_idx = futures[future]
                try:
                    ids = future.result()
                    uploaded_ids.extend(ids)
                    pbar.update(len(batches[batch_idx]))
                except Exception as e:
                    failed_batches.append((batch_idx, str(e)))
                    pbar.update(len(batches[batch_idx]))

    print(f"\n✓ Upload complete!")
    print(f"  Successful: {len(uploaded_ids)} documents")
    if failed_batches:
        print(f"  Failed: {len(failed_batches)} batches")
        for batch_idx, error in failed_batches:
            print(f"    - Batch {batch_idx}: {error}")

    return uploaded_ids


def create_payload_index(client: QdrantClient, collection_name: str) -> None:
    """Create payload index for source filtering."""
    from qdrant_client.models import PayloadSchemaType

    print("\nCreating payload index on metadata.source_name...")
    try:
        client.create_payload_index(
            collection_name=collection_name,
            field_name='metadata.source_name',
            field_schema=PayloadSchemaType.KEYWORD,
        )
        print("✓ Payload index created")
    except Exception as e:
        print(f"  Warning: Could not create index: {e}")


def save_upload_manifest(
    output_path: Path,
    uploaded_count: int,
    total_count: int,
    collection_name: str,
    sources: List[str],
) -> None:
    """Save upload metadata."""
    manifest = {
        "upload_timestamp": datetime.now().isoformat(),
        "collection_name": collection_name,
        "documents_uploaded": uploaded_count,
        "documents_total": total_count,
        "sources": sources,
        "embedding_model": "BAAI/bge-small-en-v1.5 (FastEmbed)",
        "embedding_dimension": EMBEDDING_DIMENSION,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"\n✓ Upload manifest saved to {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Upload documentation pages to Qdrant using FastEmbed (free, no API key)"
    )
    parser.add_argument(
        "--sources",
        nargs="+",
        help="Filter to specific sources",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=BATCH_SIZE,
        help=f"Documents per batch (default: {BATCH_SIZE})",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=MAX_WORKERS,
        help=f"Parallel workers (default: {MAX_WORKERS})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate without uploading",
    )
    parser.add_argument(
        "--collection",
        default=COLLECTION_NAME,
        help=f"Collection name (default: {COLLECTION_NAME})",
    )

    args = parser.parse_args()

    print("=" * 70)
    print("Qdrant Documentation Upload (FastEmbed - Free)")
    print("=" * 70)
    print("Embedding model: BAAI/bge-small-en-v1.5 (384 dimensions)")
    print("No OpenAI API key required!")

    try:
        print("\nInitializing clients...")
        client = init_qdrant_client()
        embeddings = get_fastembed_embeddings()
        print("✓ Clients initialized")

        if not args.dry_run:
            create_collection(client, args.collection)

        documents = load_documents(
            PAGES_DIR,
            source_filter=args.sources,
        )

        if not documents:
            print("\n✗ No documents found to upload")
            return

        uploaded_ids = upload_documents(
            documents=documents,
            embeddings=embeddings,
            client=client,
            collection_name=args.collection,
            batch_size=args.batch_size,
            max_workers=args.workers,
            dry_run=args.dry_run,
        )

        if not args.dry_run and uploaded_ids:
            # Create payload index for filtering
            create_payload_index(client, args.collection)

            sources = list(set(doc.metadata["source_name"] for doc in documents))
            manifest_path = PROCESSED_DIR / "upload_manifest_fastembed.json"
            save_upload_manifest(
                manifest_path,
                len(uploaded_ids),
                len(documents),
                args.collection,
                sources,
            )

        print("\n" + "=" * 70)
        print("Upload Complete!")
        print("=" * 70)

    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
        exit(1)


if __name__ == "__main__":
    main()

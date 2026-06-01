#!/usr/bin/env python3
"""
Upload documentation pages to Qdrant Cloud.

This script loads JSON documents from data/interim/pages/, converts them to
LangChain Documents with metadata from manifest.json, and uploads them to
Qdrant Cloud using OpenAI embeddings.
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
from langchain_qdrant import QdrantVectorStore
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams
from tqdm import tqdm

from embedding_config import get_embeddings

# Load environment variables
load_dotenv()

# Constants
COLLECTION_NAME = "platform-docs"
EMBEDDING_DIMENSION = 1536
BATCH_SIZE = 100
MAX_WORKERS = 4
DATA_DIR = Path(__file__).parent.parent / "data"
PAGES_DIR = DATA_DIR / "interim" / "pages"
PROCESSED_DIR = DATA_DIR / "processed"


def init_qdrant_client() -> QdrantClient:
    """
    Initialize Qdrant client with credentials from environment.

    Returns:
        QdrantClient: Configured Qdrant client

    Raises:
        ValueError: If required environment variables are missing
    """
    api_url = os.getenv("QDRANT_API_URL")
    api_key = os.getenv("QDRANT_API_KEY")

    if not api_url or not api_key:
        raise ValueError(
            "QDRANT_API_URL and QDRANT_API_KEY must be set in environment variables. "
            "Please add them to your .env file."
        )

    return QdrantClient(
        url=api_url,
        api_key=api_key,
        prefer_grpc=True,
    )


def create_collection(client: QdrantClient, collection_name: str) -> None:
    """
    Create Qdrant collection if it doesn't exist.

    Args:
        client: Qdrant client instance
        collection_name: Name of the collection to create
    """
    if client.collection_exists(collection_name):
        print(f"✓ Collection '{collection_name}' already exists")
        return

    print(f"Creating collection '{collection_name}'...")
    client.create_collection(
        collection_name=collection_name,
        vectors_config=VectorParams(
            size=EMBEDDING_DIMENSION,
            distance=Distance.COSINE,
            on_disk=True,  # Store vectors on disk to save RAM
        ),
        optimizers_config={
            "indexing_threshold": 10000,
        },
        on_disk_payload=True,  # Store payload on disk
    )
    print(f"✓ Collection '{collection_name}' created successfully")


def load_documents(
    pages_dir: Path,
    source_filter: Optional[List[str]] = None,
) -> List[Document]:
    """
    Load all JSON files and convert to LangChain Documents with manifest metadata.

    Args:
        pages_dir: Path to directory containing source subdirectories with JSON files
        source_filter: Optional list of source names to include (e.g., ["LangChain", "Anthropic"])

    Returns:
        List of LangChain Document objects with metadata
    """
    # Load manifest for source-level metadata
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

        # Get manifest metadata for this source
        source_manifest = manifest["results"].get(source_name, {})
        if not source_manifest:
            print(f"  Warning: {source_name} not found in manifest")
            continue

        json_files = [f for f in source_dir.glob("*.json") if f.name != "manifest.json"]
        print(f"  Loading {len(json_files)} documents from {source_name}...")

        for json_file in json_files:
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Extract page number from filename (e.g., "0350_Title.json" -> "0350")
            page_num = json_file.stem.split("_")[0]

            doc = Document(
                page_content=data["content"],  # Full content, no chunking
                metadata={
                    # From individual JSON file
                    "title": data.get("title") or "Untitled",
                    "source_url": data.get("source_url"),
                    "content_length": data["content_length"],
                    # Hierarchy metadata (for multi-level sources like Temporal)
                    "header_level": data.get("header_level"),
                    "section_path": data.get("section_path"),
                    "parent_title": data.get("parent_title"),
                    "parent_index": data.get("parent_index"),
                    # From manifest.json
                    "source_name": source_name,
                    "total_pages": source_manifest.get("page_count"),
                    "avg_content_length": source_manifest.get("avg_size_chars"),
                    # Generated
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
    """
    Upload a single batch of documents.

    Args:
        batch: List of documents to upload
        vector_store: QdrantVectorStore instance
        batch_idx: Index of the batch (for logging)

    Returns:
        List of document IDs that were uploaded
    """
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
    """
    Upload documents in batches with parallel processing.

    Args:
        documents: List of LangChain documents to upload
        embeddings: Embedding model instance
        client: Qdrant client instance
        collection_name: Name of the collection to upload to
        batch_size: Number of documents per batch
        max_workers: Number of parallel workers
        dry_run: If True, skip actual upload

    Returns:
        List of uploaded document IDs
    """
    if dry_run:
        print(f"\n[DRY RUN] Would upload {len(documents)} documents to '{collection_name}'")
        print(f"[DRY RUN] Batch size: {batch_size}, Workers: {max_workers}")
        return []

    # Initialize vector store
    vector_store = QdrantVectorStore(
        client=client,
        collection_name=collection_name,
        embedding=embeddings,
    )

    # Split into batches
    batches = [
        documents[i : i + batch_size] for i in range(0, len(documents), batch_size)
    ]

    print(f"\nUploading {len(documents)} documents in {len(batches)} batches...")
    print(f"Batch size: {batch_size}, Parallel workers: {max_workers}")

    uploaded_ids = []
    failed_batches = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all batches
        futures = {
            executor.submit(upload_batch, batch, vector_store, idx): idx
            for idx, batch in enumerate(batches)
        }

        # Process results with progress bar
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

    # Report results
    print(f"\n✓ Upload complete!")
    print(f"  Successful: {len(uploaded_ids)} documents")
    if failed_batches:
        print(f"  Failed: {len(failed_batches)} batches")
        for batch_idx, error in failed_batches:
            print(f"    - Batch {batch_idx}: {error}")

    return uploaded_ids


def save_upload_manifest(
    output_path: Path,
    uploaded_count: int,
    total_count: int,
    collection_name: str,
    sources: List[str],
) -> None:
    """
    Save upload metadata for tracking.

    Args:
        output_path: Path to save manifest JSON
        uploaded_count: Number of documents successfully uploaded
        total_count: Total number of documents attempted
        collection_name: Name of the Qdrant collection
        sources: List of source names that were uploaded
    """
    manifest = {
        "upload_timestamp": datetime.now().isoformat(),
        "collection_name": collection_name,
        "documents_uploaded": uploaded_count,
        "documents_total": total_count,
        "sources": sources,
        "embedding_model": "text-embedding-3-small",
        "embedding_dimension": EMBEDDING_DIMENSION,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"\n✓ Upload manifest saved to {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Upload documentation pages to Qdrant Cloud"
    )
    parser.add_argument(
        "--sources",
        nargs="+",
        help="Filter to specific sources (e.g., --sources LangChain Anthropic)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=BATCH_SIZE,
        help=f"Number of documents per batch (default: {BATCH_SIZE})",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=MAX_WORKERS,
        help=f"Number of parallel workers (default: {MAX_WORKERS})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate configuration without uploading",
    )
    parser.add_argument(
        "--collection",
        default=COLLECTION_NAME,
        help=f"Qdrant collection name (default: {COLLECTION_NAME})",
    )

    args = parser.parse_args()

    print("=" * 70)
    print("Qdrant Documentation Upload")
    print("=" * 70)

    try:
        # Initialize clients
        print("\nInitializing clients...")
        client = init_qdrant_client()
        embeddings = get_embeddings()
        print("✓ Clients initialized")

        # Create collection
        if not args.dry_run:
            create_collection(client, args.collection)

        # Load documents
        documents = load_documents(
            PAGES_DIR,
            source_filter=args.sources,
        )

        if not documents:
            print("\n✗ No documents found to upload")
            return

        # Upload documents
        uploaded_ids = upload_documents(
            documents=documents,
            embeddings=embeddings,
            client=client,
            collection_name=args.collection,
            batch_size=args.batch_size,
            max_workers=args.workers,
            dry_run=args.dry_run,
        )

        # Save manifest
        if not args.dry_run and uploaded_ids:
            sources = list(set(doc.metadata["source_name"] for doc in documents))
            manifest_path = PROCESSED_DIR / "upload_manifest.json"
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

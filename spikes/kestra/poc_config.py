"""Shared constants and helpers for the Kestra spike. Production-safe by design."""
from pathlib import Path

POC_SOURCES = ["OpenAI", "Vue", "Supabase"]
POC_COLLECTION = "platform-docs-poc-v1"
POC_COLLECTION_FASTEMBED = "platform-docs-poc-fastembed-v1"
POC_ALIAS = "platform-docs-poc-active"
POC_ALIAS_FASTEMBED = "platform-docs-fastembed-poc-active"

PROD_ALIASES = frozenset({"platform-docs", "platform-docs-fastembed"})
PROD_COLLECTIONS = frozenset({"platform-docs-v2", "platform-docs-fastembed-v2"})

DEFAULT_PAGES_DIR = Path(__file__).resolve().parents[2] / "data" / "interim" / "pages"


def expected_doc_count(sources: list[str], pages_dir: Path = DEFAULT_PAGES_DIR) -> int:
    """Count split page JSON files (excluding manifest.json) across the given sources."""
    total = 0
    for source in sources:
        source_dir = pages_dir / source
        if not source_dir.is_dir():
            continue
        total += sum(1 for f in source_dir.glob("*.json") if f.name != "manifest.json")
    return total

"""
Embedding configuration for Qdrant upload.

This module provides the embedding model configuration using OpenAI's text-embedding-3-small.
"""

from langchain_openai import OpenAIEmbeddings
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Full-corpus refreshes hit OpenAI's 5M TPM limit (429s). Retrying is safe because a failed
# embed call happens before any Qdrant upsert, so retries add no duplicate points.
EMBEDDING_MAX_RETRIES = 8


def get_embeddings():
    """
    Initialize OpenAI text-embedding-3-small embeddings.

    Returns:
        OpenAIEmbeddings: Configured embedding model instance

    Raises:
        ValueError: If OPENAI_API_KEY is not set in environment variables
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError(
            "OPENAI_API_KEY not found in environment variables. "
            "Please add it to your .env file."
        )

    return OpenAIEmbeddings(
        model="text-embedding-3-small",
        openai_api_key=api_key,
        dimensions=1536,  # Default dimension for text-embedding-3-small
        max_retries=EMBEDDING_MAX_RETRIES,
    )


if __name__ == "__main__":
    # Test embedding configuration
    print("Testing embedding configuration...")
    try:
        embeddings = get_embeddings()

        # Test with a sample query
        test_text = "This is a test document."
        print(f"Embedding test text: '{test_text}'")

        vector = embeddings.embed_query(test_text)
        print(f"✓ Embedding successful!")
        print(f"  Vector dimension: {len(vector)}")
        print(f"  First 5 values: {vector[:5]}")

    except Exception as e:
        print(f"✗ Error: {e}")

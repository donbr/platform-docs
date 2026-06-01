#!/usr/bin/env python3
"""
Download llms.txt and llms-full.txt files from documentation sources.

Usage:
    uv run scripts-temp/download_llms_raw.py

Downloads files to data/raw/{source}/ directory structure:
    data/raw/Cursor/llms.txt
    data/raw/Cursor/llms-full.txt
    ...
"""

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

# Source definitions: name -> (llms.txt URL, llms-full.txt URL)
SOURCES = {
    'Cursor': (
        'https://docs.cursor.com/llms.txt',
        'https://docs.cursor.com/llms-full.txt',
    ),
    'PydanticAI': (
        'https://ai.pydantic.dev/llms.txt',
        'https://ai.pydantic.dev/llms-full.txt',
    ),
    'McpProtocol': (
        'https://modelcontextprotocol.io/llms.txt',
        'https://modelcontextprotocol.io/llms-full.txt',
    ),
    'FastMCP': (
        'https://gofastmcp.com/llms.txt',
        'https://gofastmcp.com/llms-full.txt',
    ),
    'LangChain': (
        'https://docs.langchain.com/llms.txt',
        'https://docs.langchain.com/llms-full.txt',
    ),
    'Prefect': (
        'https://docs.prefect.io/llms.txt',
        'https://docs.prefect.io/llms-full.txt',
    ),
    'Anthropic': (
        'https://platform.claude.com/llms.txt',
        'https://platform.claude.com/llms-full.txt',
    ),
    'OpenAI': (
        'https://cdn.openai.com/API/docs/txt/llms.txt',
        'https://cdn.openai.com/API/docs/txt/llms-full.txt',
    ),
    'Vue': (
        'https://vuejs.org/llms.txt',
        'https://vuejs.org/llms-full.txt',
    ),
    'Supabase': (
        'https://supabase.com/docs/llms.txt',
        'https://supabase.com/llms-full.txt',
    ),
    'Zep': (
        'https://help.getzep.com/llms.txt',
        'https://help.getzep.com/llms-full.txt',
    ),
    'Temporal': (
        'https://docs.temporal.io/llms.txt',
        'https://docs.temporal.io/llms-full.txt',
    ),
    'GoogleADK': (
        'https://google.github.io/adk-docs/llms.txt',
        'https://google.github.io/adk-docs/llms-full.txt',
    ),
}

# Output directory
OUTPUT_DIR = Path(__file__).parent.parent / 'data' / 'raw'


async def download_file(
    client: httpx.AsyncClient, url: str, output_path: Path
) -> dict:
    """Download a single file.

    Returns:
        Dict with status, size_bytes, and error (if any)
    """
    try:
        response = await client.get(url, follow_redirects=True)
        response.raise_for_status()

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(response.text, encoding='utf-8')

        return {
            'status': 'success',
            'size_bytes': len(response.text),
            'url': url,
        }

    except httpx.HTTPStatusError as e:
        return {
            'status': 'failed',
            'error': f'HTTP {e.response.status_code}',
            'size_bytes': 0,
            'url': url,
        }
    except httpx.RequestError as e:
        return {
            'status': 'failed',
            'error': f'Request failed: {type(e).__name__}',
            'size_bytes': 0,
            'url': url,
        }
    except Exception as e:
        return {
            'status': 'failed',
            'error': str(e),
            'size_bytes': 0,
            'url': url,
        }


async def download_source(
    client: httpx.AsyncClient, name: str, urls: tuple[str, str]
) -> dict:
    """Download both llms.txt and llms-full.txt for a source.

    Returns:
        Dict with results for both files
    """
    llms_url, llms_full_url = urls
    source_dir = OUTPUT_DIR / name

    # Download both files concurrently
    llms_result, llms_full_result = await asyncio.gather(
        download_file(client, llms_url, source_dir / 'llms.txt'),
        download_file(client, llms_full_url, source_dir / 'llms-full.txt'),
    )

    return {
        'llms.txt': llms_result,
        'llms-full.txt': llms_full_result,
    }


async def main():
    """Download all llms.txt and llms-full.txt files."""
    # Ensure output directory exists
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f'Downloading from {len(SOURCES)} sources to {OUTPUT_DIR}\n')

    start_time = datetime.now(timezone.utc)

    async with httpx.AsyncClient(timeout=60.0) as client:
        # Download all sources concurrently
        tasks = [
            download_source(client, name, urls) for name, urls in SOURCES.items()
        ]
        results = await asyncio.gather(*tasks)

    # Build results dict
    source_results = dict(zip(SOURCES.keys(), results))

    # Report results
    total_llms = 0
    total_llms_full = 0
    failed_llms = 0
    failed_llms_full = 0

    for name, result in sorted(source_results.items()):
        llms = result['llms.txt']
        llms_full = result['llms-full.txt']

        llms_status = '✓' if llms['status'] == 'success' else '✗'
        llms_full_status = '✓' if llms_full['status'] == 'success' else '✗'

        llms_size = f"{llms['size_bytes'] / 1024:.1f} KB" if llms['status'] == 'success' else llms.get('error', 'failed')
        llms_full_size = f"{llms_full['size_bytes'] / 1024:.1f} KB" if llms_full['status'] == 'success' else llms_full.get('error', 'failed')

        print(f'  {name}:')
        print(f'    {llms_status} llms.txt: {llms_size}')
        print(f'    {llms_full_status} llms-full.txt: {llms_full_size}')

        if llms['status'] == 'success':
            total_llms += 1
        else:
            failed_llms += 1

        if llms_full['status'] == 'success':
            total_llms_full += 1
        else:
            failed_llms_full += 1

    end_time = datetime.now(timezone.utc)

    # Generate manifest
    manifest = {
        'created_at': end_time.isoformat(),
        'duration_seconds': (end_time - start_time).total_seconds(),
        'sources': source_results,
        'summary': {
            'total_sources': len(SOURCES),
            'llms_txt': {'success': total_llms, 'failed': failed_llms},
            'llms_full_txt': {'success': total_llms_full, 'failed': failed_llms_full},
        },
    }

    manifest_path = OUTPUT_DIR / 'manifest.json'
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')

    print(f'\n  Summary:')
    print(f'    llms.txt: {total_llms} succeeded, {failed_llms} failed')
    print(f'    llms-full.txt: {total_llms_full} succeeded, {failed_llms_full} failed')
    print(f'    Duration: {manifest["duration_seconds"]:.1f}s')
    print(f'    Manifest: {manifest_path}')

    return 0 if (failed_llms == 0 and failed_llms_full == 0) else 1


if __name__ == '__main__':
    exit(asyncio.run(main()))

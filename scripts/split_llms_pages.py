#!/usr/bin/env python3
"""
Split llms-full.txt files into individual pages/documents.

Usage:
    uv run scripts-temp/split_llms_pages.py

Three splitting strategies:
1. Source URL pattern: ^# .*$\nSource: (for LangChain, Anthropic, etc.)
2. Header-only pattern: ^# (for PydanticAI, Zep - filters out code block false positives)
3. Multi-level pattern: ^#{1,2} (for Temporal - splits on # and ## headers)

Outputs to data/interim/pages/{source}/ directory.
"""

import json
import re
import shutil
from pathlib import Path

# Directories
RAW_DIR = Path(__file__).parent.parent / 'data' / 'raw'
OUTPUT_DIR = Path(__file__).parent.parent / 'data' / 'interim' / 'pages'

# Sources with Source: URL pattern
SOURCES_WITH_URL = [
    'LangChain',
    'Anthropic',
    'Prefect',
    'FastMCP',
    'McpProtocol',
]

# Sources with header-only pattern (no Source: line)
SOURCES_HEADER_ONLY = [
    'PydanticAI',
    'Zep',
    'GoogleADK',
]

# Sources with multi-level header pattern (split on # and ##)
SOURCES_MULTI_LEVEL = [
    'Temporal',
]

# Minimum content length (chars) for a page to be kept. Pages below this are
# considered API-reference stubs (e.g., gofastmcp.com/python-sdk/...-__init__)
# and would otherwise dominate top-k for short technical queries.
MIN_CONTENT_LENGTH = 200

# Regex patterns
# Pattern 1: # Title followed by Source: URL (original format)
PAGE_PATTERN_WITH_URL = re.compile(r'^# (.+)$\nSource: (https?://[^\n]+)', re.MULTILINE)

# Pattern 1b: # Title followed by blank line then URL: (Anthropic new format)
PAGE_PATTERN_WITH_URL_ALT = re.compile(r'^# (.+)$\n\nURL: (https?://[^\n]+)', re.MULTILINE)

# Pattern 2: # Title at start of line (outside code blocks)
PAGE_PATTERN_HEADER_ONLY = re.compile(r'^# (.+)$', re.MULTILINE)

# Pattern 3: # Title OR ## Title at start of line (outside code blocks)
PAGE_PATTERN_MULTI_LEVEL = re.compile(r'^(#{1,2})\s+(.+)$', re.MULTILINE)

# Pattern 4: Grok ===/path=== page delimiter (docs.x.ai/llms.txt). The path is
# the canonical URL path, e.g. ===/grok/connectors===.
PAGE_PATTERN_GROK = re.compile(r'^===/(.+?)===$', re.MULTILINE)

# Base URL used to rebuild canonical source URLs from Grok delimiter paths.
GROK_BASE_URL = 'https://docs.x.ai/'


def neutralize_code_block_headers(content: str) -> str:
    """Convert # headers inside code blocks to ### to avoid false positive matches.

    This preserves the code block content while preventing ^# from matching
    Python comments or markdown headers inside code examples.
    """

    def replace_headers_in_block(match: re.Match) -> str:
        """Replace ^# with ^### inside a code block."""
        block = match.group(0)
        # Replace lines starting with "# " (Python comments) with "### "
        # This changes character count, so we can't use position-based extraction
        return re.sub(r'^# ', '### ', block, flags=re.MULTILINE)

    return re.sub(r'```[\s\S]*?```', replace_headers_in_block, content)


def split_with_url_pattern(content: str) -> list[dict]:
    """Split content using # Title + Source: URL pattern (tries multiple formats).

    Supports two formats:
    1. Original: # Title\\nSource: URL
    2. Anthropic new: # Title\\n\\nURL: URL

    Args:
        content: Full text content of llms-full.txt

    Returns:
        List of page dicts with title, source_url, and content
    """
    pages = []

    # Try original pattern first
    matches = list(PAGE_PATTERN_WITH_URL.finditer(content))

    # If no matches, try alternate pattern (Anthropic format)
    if not matches:
        matches = list(PAGE_PATTERN_WITH_URL_ALT.finditer(content))

    for i, match in enumerate(matches):
        content_start = match.end()
        content_end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        page_content = content[content_start:content_end].strip()

        pages.append({
            'title': match.group(1).strip(),
            'source_url': match.group(2).strip(),
            'content': page_content,
            'content_length': len(page_content),
        })

    return pages


def split_with_header_pattern(content: str) -> list[dict]:
    """Split content using ^# Title pattern, filtering out code block headers.

    Strategy: Remove code blocks (preserving line count), find headers in cleaned
    content, then use those same positions in the original content since line
    counts are preserved.

    Args:
        content: Full text content of llms-full.txt

    Returns:
        List of page dicts with title, source_url (None), and content
    """
    # Neutralize headers inside code blocks to avoid false matches
    content_neutralized = neutralize_code_block_headers(content)

    pages = []
    matches = list(PAGE_PATTERN_HEADER_ONLY.finditer(content_neutralized))

    for i, match in enumerate(matches):
        # Extract from neutralized content (code blocks have ### instead of #)
        content_start = match.end()
        content_end = matches[i + 1].start() if i + 1 < len(matches) else len(content_neutralized)

        # Extract from neutralized content - code blocks will show ### for comments
        page_content = content_neutralized[content_start:content_end].strip()

        pages.append({
            'title': match.group(1).strip(),
            'source_url': None,  # No source URL in this format
            'content': page_content,
            'content_length': len(page_content),
        })

    return pages


def split_with_multi_level_pattern(content: str, levels: tuple = (1, 2)) -> list[dict]:
    """Split content using multiple header levels (# and ##), filtering out code blocks.

    Tracks parent-child hierarchy for multi-level documents.

    Args:
        content: Full text content of llms-full.txt
        levels: Tuple of header levels to split on (default: (1, 2) for # and ##)

    Returns:
        List of page dicts with title, header_level, section_path, parent_title,
        parent_index, source_url (None), and content
    """
    # Neutralize headers inside code blocks to avoid false matches
    content_neutralized = neutralize_code_block_headers(content)

    pages = []
    matches = list(PAGE_PATTERN_MULTI_LEVEL.finditer(content_neutralized))

    # Filter matches to only include specified levels
    level_matches = []
    for match in matches:
        header_marker = match.group(1)  # The # or ## part
        header_level = len(header_marker)
        if header_level in levels:
            level_matches.append((match, header_level))

    # Track current level 1 parent for hierarchy
    current_parent_title = None
    current_parent_index = None

    for i, (match, header_level) in enumerate(level_matches):
        title = match.group(2).strip()  # The title text

        # Extract content from neutralized version
        content_start = match.end()
        content_end = level_matches[i + 1][0].start() if i + 1 < len(level_matches) else len(content_neutralized)
        page_content = content_neutralized[content_start:content_end].strip()

        # Build hierarchy metadata
        if header_level == 1:
            # Level 1 headers are top-level sections (no parent)
            parent_title = None
            parent_index = None
            section_path = title

            # Update current parent for subsequent level 2 headers
            current_parent_title = title
            current_parent_index = i
        else:
            # Level 2+ headers are children of most recent level 1
            parent_title = current_parent_title
            parent_index = current_parent_index

            if parent_title:
                section_path = f"{parent_title} > {title}"
            else:
                # Edge case: level 2 header before any level 1 header
                section_path = title

        pages.append({
            'title': title,
            'header_level': header_level,
            'section_path': section_path,
            'parent_title': parent_title,
            'parent_index': parent_index,
            'source_url': None,  # No source URL in this format
            'content': page_content,
            'content_length': len(page_content),
        })

    return pages


def split_with_grok_pattern(content: str) -> list[dict]:
    """Split content using the Grok ===/path=== page delimiter.

    Each page is delimited by a line like ``===/grok/connectors===``. The path
    becomes the canonical source_url; the title is the page's first ``# `` H1
    (falling back to the last path segment when a page has no H1, e.g. an H4
    eyebrow only). Code-block headers are neutralized first so a ``# `` comment
    inside a fenced block is never mistaken for the title.

    Args:
        content: Full text content of the Grok llms.txt corpus.

    Returns:
        List of page dicts with title, source_url, content, and content_length.
    """
    # Neutralize headers inside code blocks to avoid false title/header matches.
    content_neutralized = neutralize_code_block_headers(content)

    pages = []
    matches = list(PAGE_PATTERN_GROK.finditer(content_neutralized))

    for i, match in enumerate(matches):
        path = match.group(1).strip()

        block_start = match.end()
        block_end = matches[i + 1].start() if i + 1 < len(matches) else len(content_neutralized)
        block = content_neutralized[block_start:block_end]

        # Title = first H1 (^# ) in the block; fall back to last path segment.
        h1 = re.search(r'^# (.+)$', block, re.MULTILINE)
        if h1:
            title = h1.group(1).strip()
            page_content = block[h1.end():].strip()
        else:
            title = path.rstrip('/').split('/')[-1] or path
            page_content = block.strip()

        pages.append({
            'title': title,
            'source_url': GROK_BASE_URL + path,
            'content': page_content,
            'content_length': len(page_content),
        })

    return pages


def process_source(source_name: str, use_header_only: bool = False, use_multi_level: bool = False) -> dict:
    """Process a single source's llms-full.txt file.

    Args:
        source_name: Name of the source (directory name)
        use_header_only: If True, use header-only pattern instead of URL pattern
        use_multi_level: If True, use multi-level header pattern (# and ##)

    Returns:
        Dict with processing results
    """
    input_path = RAW_DIR / source_name / 'llms-full.txt'
    output_dir = OUTPUT_DIR / source_name

    if not input_path.exists():
        return {
            'status': 'skipped',
            'error': f'File not found: {input_path}',
            'page_count': 0,
        }

    # Read content
    content = input_path.read_text(encoding='utf-8')

    # Split into pages using appropriate pattern
    if use_multi_level:
        pages = split_with_multi_level_pattern(content)
        pattern_type = 'multi_level'
    elif use_header_only:
        pages = split_with_header_pattern(content)
        pattern_type = 'header_only'
    else:
        pages = split_with_url_pattern(content)
        pattern_type = 'with_url'

    # Drop stub pages (e.g., empty __init__ module references with only ~40 chars)
    # to prevent them from dominating top-k for short technical queries.
    original_count = len(pages)
    pages = [p for p in pages if p['content_length'] >= MIN_CONTENT_LENGTH]
    dropped = original_count - len(pages)

    if not pages:
        return {
            'status': 'failed',
            'error': 'No pages found - check regex pattern',
            'page_count': 0,
            'pages_dropped': dropped,
        }

    # Clean and recreate output directory so stale JSON files from prior runs
    # (e.g., pages now filtered out, or pages with different titles at the same
    # index) don't linger and get picked up by the uploader's *.json glob.
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Write individual page files as JSON
    for i, page in enumerate(pages):
        # Create safe filename from title
        safe_title = re.sub(r'[^\w\s-]', '', page['title'])[:50].strip()
        safe_title = re.sub(r'\s+', '_', safe_title)
        filename = f'{i:04d}_{safe_title}.json'

        page_path = output_dir / filename
        page_path.write_text(json.dumps(page, indent=2, ensure_ascii=False), encoding='utf-8')

    # Write summary manifest
    manifest = {
        'source': source_name,
        'input_file': str(input_path),
        'pattern_type': pattern_type,
        'page_count': len(pages),
        'total_content_chars': sum(p['content_length'] for p in pages),
        'avg_page_size': sum(p['content_length'] for p in pages) / len(pages),
        'pages': [
            {
                'index': i,
                'title': p['title'],
                'header_level': p.get('header_level'),  # Include if present
                'section_path': p.get('section_path'),  # Include if present
                'parent_title': p.get('parent_title'),  # Include if present
                'parent_index': p.get('parent_index'),  # Include if present
                'source_url': p['source_url'],
                'content_length': p['content_length'],
            }
            for i, p in enumerate(pages)
        ],
    }

    manifest_path = output_dir / 'manifest.json'
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding='utf-8')

    return {
        'status': 'success',
        'page_count': len(pages),
        'pages_dropped': dropped,
        'avg_size_chars': manifest['avg_page_size'],
        'output_dir': str(output_dir),
    }


def main():
    """Process all sources and split into pages."""
    print('Splitting llms-full.txt files into pages')
    print(f'Raw directory: {RAW_DIR}')
    print(f'Output directory: {OUTPUT_DIR}')
    print()

    # Ensure output directory exists
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    results = {}
    total_pages = 0

    # Process sources with URL pattern
    print('=== Sources with Source: URL pattern ===')
    for source in SOURCES_WITH_URL:
        print(f'Processing {source}...', end=' ')
        result = process_source(source, use_header_only=False)
        results[source] = result

        if result['status'] == 'success':
            print(f'✓ {result["page_count"]} pages ({result["avg_size_chars"]:.0f} chars avg, '
                  f'{result["pages_dropped"]} stubs dropped)')
            total_pages += result['page_count']
        else:
            print(f'✗ {result.get("error", "unknown error")}')

    # Process sources with header-only pattern
    print()
    print('=== Sources with header-only pattern ===')
    for source in SOURCES_HEADER_ONLY:
        print(f'Processing {source}...', end=' ')
        result = process_source(source, use_header_only=True)
        results[source] = result

        if result['status'] == 'success':
            print(f'✓ {result["page_count"]} pages ({result["avg_size_chars"]:.0f} chars avg, '
                  f'{result["pages_dropped"]} stubs dropped)')
            total_pages += result['page_count']
        else:
            print(f'✗ {result.get("error", "unknown error")}')

    # Process sources with multi-level header pattern
    print()
    print('=== Sources with multi-level header pattern (# and ##) ===')
    for source in SOURCES_MULTI_LEVEL:
        print(f'Processing {source}...', end=' ')
        result = process_source(source, use_multi_level=True)
        results[source] = result

        if result['status'] == 'success':
            print(f'✓ {result["page_count"]} pages ({result["avg_size_chars"]:.0f} chars avg, '
                  f'{result["pages_dropped"]} stubs dropped)')
            total_pages += result['page_count']
        else:
            print(f'✗ {result.get("error", "unknown error")}')

    # Write overall manifest
    all_sources = SOURCES_WITH_URL + SOURCES_HEADER_ONLY + SOURCES_MULTI_LEVEL
    overall_manifest = {
        'sources_processed': len([r for r in results.values() if r['status'] == 'success']),
        'total_pages': total_pages,
        'results': results,
    }

    overall_manifest_path = OUTPUT_DIR / 'manifest.json'
    overall_manifest_path.write_text(
        json.dumps(overall_manifest, indent=2, ensure_ascii=False), encoding='utf-8'
    )

    print()
    print(f'Summary: {total_pages} total pages from {len(all_sources)} sources')
    print(f'Manifest: {overall_manifest_path}')


if __name__ == '__main__':
    main()

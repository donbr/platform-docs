"""Unit tests for the Grok (===/path===) split strategy in split_llms_pages."""
from scripts.split_llms_pages import split_with_grok_pattern

# Three-page fixture: a normal page, a page with an H4 eyebrow before its H1,
# and a tiny page with no H1 (exercises the path-segment title fallback).
GROK_FIXTURE = """===/overview===
# Get started with xAI

Build with Grok, the AI model designed to deliver truthful, insightful answers.

## Models

We offer a range of models supporting multiple use cases and modalities.

===/grok/connectors===
#### Grok

# Connectors

Connectors are available to all Grok users and let Grok access your external
tools and data sources directly within a conversation.

===/developers/tiny===
short
"""

# Page whose only "# " line lives inside a code block (no real H1).
GROK_CODEBLOCK_FIXTURE = """===/developers/snippet===
Intro prose with no markdown H1 title on this page, but long enough to matter.

```bash
# setup the client
export KEY=abc
```

Closing prose.
"""


def test_splits_each_delimited_page():
    pages = split_with_grok_pattern(GROK_FIXTURE)
    assert len(pages) == 3
    assert [p["title"] for p in pages] == ["Get started with xAI", "Connectors", "tiny"]


def test_title_ignores_h4_eyebrow():
    pages = split_with_grok_pattern(GROK_FIXTURE)
    connectors = pages[1]
    assert connectors["title"] == "Connectors"
    assert "# Connectors" not in connectors["content"]
    assert "#### Grok" not in connectors["content"]
    assert connectors["content"].startswith("Connectors are available")


def test_source_url_derived_from_delimiter_path():
    pages = split_with_grok_pattern(GROK_FIXTURE)
    assert pages[0]["source_url"] == "https://docs.x.ai/overview"
    assert pages[1]["source_url"] == "https://docs.x.ai/grok/connectors"
    assert pages[2]["source_url"] == "https://docs.x.ai/developers/tiny"


def test_title_falls_back_to_last_path_segment_when_no_h1():
    pages = split_with_grok_pattern(GROK_FIXTURE)
    tiny = pages[2]
    assert tiny["title"] == "tiny"
    assert tiny["content"] == "short"
    assert tiny["content_length"] == len("short")


def test_code_block_header_not_selected_as_title():
    pages = split_with_grok_pattern(GROK_CODEBLOCK_FIXTURE)
    assert len(pages) == 1
    page = pages[0]
    assert page["title"] == "snippet"
    assert "### setup the client" in page["content"]

from types import SimpleNamespace

import pytest

from spikes.kestra import reset_target


def _alias(alias_name, collection_name):
    return SimpleNamespace(alias_name=alias_name, collection_name=collection_name)


ALIASES = [
    _alias("platform-docs", "platform-docs-v3"),
    _alias("platform-docs-fastembed", "platform-docs-fastembed-v3"),
    _alias("platform-docs-poc-active", "platform-docs-poc-v1"),
]


def test_aliases_pointing_at_live_collection():
    assert reset_target.aliases_pointing_at("platform-docs-v3", ALIASES) == ["platform-docs"]


def test_aliases_pointing_at_multiple_sorted():
    extra = ALIASES + [_alias("another", "platform-docs-v3")]
    assert reset_target.aliases_pointing_at("platform-docs-v3", extra) == ["another", "platform-docs"]


def test_aliases_pointing_at_unaliased_collection():
    assert reset_target.aliases_pointing_at("platform-docs-v4", ALIASES) == []


def test_aliases_pointing_at_empty_list():
    assert reset_target.aliases_pointing_at("x", []) == []


def test_assert_safe_refuses_live_collection():
    with pytest.raises(ValueError, match="platform-docs"):
        reset_target.assert_safe_to_reset("platform-docs-v3", ALIASES)


def test_assert_safe_names_all_aliases():
    extra = ALIASES + [_alias("another", "platform-docs-v3")]
    with pytest.raises(ValueError) as e:
        reset_target.assert_safe_to_reset("platform-docs-v3", extra)
    assert "another" in str(e.value) and "platform-docs" in str(e.value)


def test_assert_safe_allows_unaliased():
    reset_target.assert_safe_to_reset("platform-docs-v4", ALIASES)

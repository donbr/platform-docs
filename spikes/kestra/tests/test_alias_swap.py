import pytest

from spikes.kestra import alias_swap


def test_guard_rejects_production_aliases():
    for name in ("platform-docs", "platform-docs-fastembed"):
        with pytest.raises(ValueError):
            alias_swap.assert_sandbox_alias(name)


def test_guard_allows_both_poc_aliases():
    alias_swap.assert_sandbox_alias("platform-docs-poc-active")
    alias_swap.assert_sandbox_alias("platform-docs-fastembed-poc-active")

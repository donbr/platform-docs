import pytest

from spikes.kestra import prod_alias_swap


def test_refuses_non_production_alias():
    with pytest.raises(ValueError):
        prod_alias_swap.assert_production_swap_allowed("platform-docs-poc-active", confirm=True)


def test_refuses_without_confirm():
    with pytest.raises(ValueError):
        prod_alias_swap.assert_production_swap_allowed("platform-docs", confirm=False)


def test_allows_confirmed_production_alias():
    prod_alias_swap.assert_production_swap_allowed("platform-docs", confirm=True)
    prod_alias_swap.assert_production_swap_allowed("platform-docs-fastembed", confirm=True)

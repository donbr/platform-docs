from spikes.kestra import verify_counts


def test_is_complete_true_when_actual_meets_expected():
    assert verify_counts.is_complete(608, 608) is True
    assert verify_counts.is_complete(609, 608) is True


def test_is_complete_false_when_short():
    assert verify_counts.is_complete(140, 608) is False


def test_resolve_expected_override_wins():
    assert verify_counts.resolve_expected(999) == 999


def test_resolve_expected_zero_computes_dynamically():
    # 0 => compute from the real split output; just assert it's a non-negative int
    n = verify_counts.resolve_expected(0)
    assert isinstance(n, int) and n >= 0

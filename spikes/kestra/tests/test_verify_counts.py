from spikes.kestra import verify_counts


def test_is_complete_true_when_actual_meets_expected():
    assert verify_counts.is_complete(608, 608) is True
    assert verify_counts.is_complete(609, 608) is True


def test_is_complete_false_when_short():
    assert verify_counts.is_complete(140, 608) is False

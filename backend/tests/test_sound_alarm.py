from app.alerts.sound_alarm import _repeat_count


def test_repeat_count_at_20_percent_is_one():
    assert _repeat_count(20) == 1


def test_repeat_count_at_15_percent_is_two():
    assert _repeat_count(15) == 2


def test_repeat_count_at_10_percent_is_three():
    assert _repeat_count(10) == 3


def test_repeat_count_at_5_percent_is_four():
    assert _repeat_count(5) == 4


def test_repeat_count_never_exceeds_four_below_five_percent():
    assert _repeat_count(0) == 4


def test_repeat_count_above_20_percent_stays_at_one():
    assert _repeat_count(25) == 1


def test_repeat_count_none_defaults_to_one():
    assert _repeat_count(None) == 1

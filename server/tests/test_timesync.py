import pytest

from wsn.timesync import ClockSync

TRUE_OFFSET = 1000.0  # server = phone + 1000 s


def sample(c0_ms, rtt_ms, server_after_ms):
    """Phone sends at c0, server stamps at c0+server_after, phone receives at c0+rtt (all phone ms)."""
    return c0_ms, TRUE_OFFSET + (c0_ms + server_after_ms) / 1000.0, c0_ms + rtt_ms


def test_min_rtt_sample_wins():
    cs = ClockSync()
    cs.add(*sample(20_000, 50, 40))    # асимметрия 15 мс
    cs.add(*sample(30_000, 200, 180))  # асимметрия 80 мс
    cs.add(*sample(10_000, 5, 2.5))    # симметричный, RTT 5 мс
    assert cs.offset == pytest.approx(TRUE_OFFSET, abs=0.003)
    assert cs.to_server(12_345) == pytest.approx(TRUE_OFFSET + 12.345, abs=0.003)


def test_keeps_last_20_samples():
    cs = ClockSync()
    cs.add(*sample(0, 1, 0.5))
    for i in range(20):
        cs.add(*sample(1000 * (i + 1), 100, 90))  # вытесняют хороший старый
    assert cs.offset == pytest.approx(TRUE_OFFSET + 0.040, abs=0.001)


def test_no_samples():
    cs = ClockSync()
    assert cs.offset is None
    with pytest.raises(ValueError):
        cs.to_server(1.0)


def test_negative_rtt_ignored():
    cs = ClockSync()
    cs.add(100, 1000.0, 50)
    assert cs.offset is None

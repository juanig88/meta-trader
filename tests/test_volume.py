"""Break-candle relative volume: ratio, bands and the Telegram line."""

from trading.domain.volume import relative_volume, volume_band, volume_text


def test_relative_volume_and_bands():
    assert relative_volume([10, 20, 30], 40) == 2.0
    assert relative_volume([], 5) is None and relative_volume([0, 0], 5) is None
    assert volume_band(0.5) == "bajo" and volume_band(1.2) == "medio" and volume_band(3) == "alto"
    assert volume_band(None) is None


def test_volume_text():
    assert "2.00x lo normal (alto)" in volume_text(2.0) and "74–79%" in volume_text(2.0)
    assert "sin dato" in volume_text(None)

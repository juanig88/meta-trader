"""M1 impulse rule: sideways then push, forming-candle agreement, range stop, fade exit."""

from __future__ import annotations

from trading.domain.impulso_m1 import BUY, SELL, Bar, ImpulseParams, detect_impulse, detect_rejection, fade_exit

# 390117817: three green M1 (18:05-18:07), red 18:08, short at the 18:09 open; HTF down except M5
PULLBACK_UP = [Bar(76865, 76949, 76865, 76924), Bar(76925, 77021, 76925, 76979),
               Bar(76980, 77068, 76980, 77054), Bar(77053, 77060, 77015, 77015)]
HTF_390117817 = [76865, 77039, 77039, 77039, 77462]


def test_climax_last_candle_is_skipped():
    # 18:52 on 30/10: the third red candle was ~3.8x a normal M1 and price bounced 500 right after
    spike = [Bar(100, 100.3, 99.4, 99.5), Bar(99.5, 99.6, 98.9, 99.0), Bar(99.0, 99.1, 91.0, 91.5)]
    assert detect_impulse(flat(12) + spike, price=91.3, forming_opens=[100] * 5) is None
    sig = detect_impulse(flat(12) + spike, price=91.3, forming_opens=[100] * 5, params=ImpulseParams(spike_mult=0))
    assert sig is not None and sig.side == SELL


def test_rejection_reproduces_user_short():
    sig = detect_rejection(PULLBACK_UP, price=77004, forming_m1_open=77012, forming_opens=HTF_390117817)
    assert sig is not None and sig.side == SELL
    assert sig.sl == 77068 and sig.tf_agree == 4


def test_rejection_needs_turn_candle_and_live_follow_through():
    no_turn = PULLBACK_UP[:3] + [Bar(77053, 77080, 77040, 77070)]
    assert detect_rejection(no_turn, 77004, 77012, HTF_390117817) is None
    assert detect_rejection(PULLBACK_UP, 77020, 77012, HTF_390117817) is None  # forming M1 not falling yet


def test_rejection_needs_higher_timeframes():
    assert detect_rejection(PULLBACK_UP, 77004, 77012, [76865, 76900, 76900, 77039, 77462]) is None


def flat(n: int, mid: float = 100.0, half: float = 1.0) -> list[Bar]:
    return [Bar(mid, mid + half, mid - half, mid + (0.2 if i % 2 else -0.2)) for i in range(n)]


def push_up() -> list[Bar]:
    return [Bar(100, 101.5, 99.8, 101), Bar(101, 102.2, 100.9, 102), Bar(102, 103.3, 101.9, 103)]


def test_buy_after_sideways_and_push():
    sig = detect_impulse(flat(12) + push_up(), price=103.5, forming_opens=[101, 100, 100, 99, 98])
    assert sig is not None and sig.side == BUY
    assert sig.sl == 99.0  # low of the sideways range
    assert sig.tf_agree == 5


def test_minute_four_must_keep_going():
    assert detect_impulse(flat(12) + push_up(), price=102.9, forming_opens=[101] * 5) is None


def test_push_smaller_than_range_is_not_a_breakout():
    wide = flat(12, half=3.0)
    assert detect_impulse(wide + push_up(), price=103.5, forming_opens=[101] * 5) is None


def test_needs_four_of_five_forming_candles():
    opens = [101, 100, 104, 105, 106]  # only M5 and M15 on our side
    assert detect_impulse(flat(12) + push_up(), price=103.5, forming_opens=opens) is None
    opens = [101, 100, 99, 98, 106]
    assert detect_impulse(flat(12) + push_up(), price=103.5, forming_opens=opens).tf_agree == 4


def test_sell_mirror_and_buffer():
    down = [Bar(100, 100.2, 98.9, 99), Bar(99, 99.1, 97.8, 98), Bar(98, 98.1, 96.7, 97)]
    sig = detect_impulse(flat(12) + down, price=96.5, forming_opens=[99] * 5,
                         params=ImpulseParams(sl_buffer=0.5))
    assert sig.side == SELL and sig.sl == 101.5


def test_one_counter_candle_breaks_the_push():
    bars = flat(12) + [Bar(100, 101.5, 99.8, 101), Bar(101, 101.2, 100.2, 100.5), Bar(100.5, 103.3, 100.4, 103)]
    assert detect_impulse(bars, price=103.5, forming_opens=[100] * 5) is None


def test_fade_exit_only_in_profit_and_after_stall():
    rising = [Bar(100, 101, 99.9, 101), Bar(101, 102, 100.9, 102)]
    stalled = rising + [Bar(102, 101.9, 101.5, 101.8), Bar(101.8, 101.95, 101.6, 101.9)]
    assert fade_exit(BUY, 100, rising, profit_usd=2.0, min_profit_usd=0.3) is None
    assert fade_exit(BUY, 100, stalled, profit_usd=1.8, min_profit_usd=0.3) == "fade_sin_maximo_nuevo"
    assert fade_exit(BUY, 100, stalled, profit_usd=0.1, min_profit_usd=0.3) is None


def test_fade_exit_two_candles_against():
    bars = [Bar(100, 101, 99.9, 101), Bar(101, 103, 100.5, 100.8), Bar(100.8, 103.5, 100.5, 100.6)]
    assert fade_exit(BUY, 100, bars, profit_usd=0.6, min_profit_usd=0.3) == "fade_velas_en_contra"

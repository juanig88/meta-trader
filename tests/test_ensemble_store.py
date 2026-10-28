from trading.infrastructure.persistence.ensemble_store import insert_ensemble_votes, insert_trade_event


def test_insert_empty_is_noop():
    assert insert_ensemble_votes([]) == 0
    assert insert_trade_event({}) == 0
    assert insert_trade_event({"event": "ENSEMBLE_VOTE"}) == 0

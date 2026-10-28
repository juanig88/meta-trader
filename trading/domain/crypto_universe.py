"""XBTFX crypto CFD universe for the rotation engine. Pure: no I/O."""

from __future__ import annotations

# Broker names from docs/improvement/lista_de_s_mbolos_y_descripciones.txt
CRYPTO_UNIVERSE: tuple[str, ...] = (
    "ADAUSD!",
    "ALGUSD!",
    "ATMUSD!",
    "AVAUSD!",
    "AXSUSD!",
    "BATUSD!",
    "BCHUSD!",
    "BTCUSD!",
    "CRVUSD!",
    "DOGUSD!",
    "DOTUSD!",
    "DSHUSD!",
    "ETCUSD!",
    "ETHUSD!",
    "FILUSD!",
    "INCUSD!",
    "IOTUSD!",
    "LNKUSD!",
    "LTCUSD!",
    "NEOUSD!",
    "NERUSD!",
    "SANUSD!",
    "SHBUSD!",
    "SOLUSD!",
    "SUSUSD!",
    "TRXUSD!",
    "UNIUSD!",
    "VECUSD!",
    "XLMUSD!",
    "XMRUSD!",
    "XRPUSD!",
    "XTZUSD!",
    "ZECUSD!",
    "CAKUSD!",
    "GRMUSD!",
    "HYPUSD!",
    "AAVUSD!",
    "ALIUSD!",
    "ANKUSD!",
    "APEUSD!",
    "ARWUSD!",
    "BARUSD!",
    "CEOUSD!",
    "CHRUSD!",
    "CHZUSD!",
    "CNEUSD!",
    "COMUSD!",
    "COTUSD!",
    "DYDUSD!",
    "EGLUSD!",
    "ENJUSD!",
    "FLWUSD!",
    "GALUSD!",
    "GMTUSD!",
    "GRTUSD!",
    "IMXUSD!",
    "IOSUSD!",
    "KAVUSD!",
    "KNCUSD!",
    "KSMUSD!",
    "LPTUSD!",
    "MANUSD!",
    "ONTUSD!",
    "PEPUSD!",
    "QTMUSD!",
    "RSRUSD!",
    "RVNUSD!",
    "SKLUSD!",
    "SNXUSD!",
    "TETUSD!",
    "WOOUSD!",
    "YFIUSD!",
    "ZENUSD!",
    "ZILUSD!",
    "ZRXUSD!",
    "ICPUSD!",
    "APTUSD!",
    "MLNUSD!",
    "TRUUSD!",
)

BTC_SYMBOL = "BTCUSD!"
ETH_SYMBOL = "ETHUSD!"
SOL_SYMBOL = "SOLUSD!"

# Stale WR < 10% with n>=20 since 2026-10-01 (IMX ~7%, VEC ~6%).
# Zero broker-net winners 2026-09-30..10-06: TRX 0/32, ZRX 0/16, RSR 0/12, AXS 0/2.
# Still scanned; a 4/5 on these names skips with rank_tail.
ROTATION_RANK_TAIL: frozenset[str] = frozenset({
    "IMXUSD!",
    "VECUSD!",
    "TRXUSD!",
    "ZRXUSD!",
    "RSRUSD!",
    "AXSUSD!",
})


def rotation_rank_skip(symbol: str) -> str:
    want = str(symbol or "").strip().upper()
    if want in ROTATION_RANK_TAIL:
        return "rank_tail"
    return ""

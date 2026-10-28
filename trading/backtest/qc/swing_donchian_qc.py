# region imports
from AlgorithmImports import *
# endregion
"""Donchian 20/10 + SMA200 daily, long only. Independent QuantConnect check of the swing engine.

Same rules as trading/domain/swing_donchian.py (levels on prior bars, exit on close
below the prior 10-day low or below SMA200, entry on close above the prior 20-day
high while above SMA200, momentum-90 ranking when slots are scarce).

Parameters (QC project parameters):
  mode      "sleeve" -> weight 1/N per symbol while on (compare analyze_swing_regime.py)
            "risk"   -> risk_frac of equity to the 10-day low, max_positions slots (compare V3 / live engine)
  universe  "majors" (BTC, ETH) or "alts" (Coinbase USD pairs that XBTFX also lists)
  start / end  yyyy-mm-dd

Costs: 0.10% of notional per side (spread + slippage + fee, same as research) and
swap 20%/year of the long notional charged daily, like an XBTFX CFD.

Agreement criteria, fixed before the first QC run (2026-10-07):
  sleeve + majors, 2018-03-05 -> 2026-09-30: CAGR > 0 in 2018-03-05..2023-08-31 and in
    2023-09-01..2026-09-30, full-period CAGR within 27.5% +/- 10 pp, max DD no worse than -45%.
  risk + alts, same full period: CAGR > 0 and max DD no worse than -35%.
  Missing any line = the two engines disagree; do not approve.

The end-of-run log prints CAGR / max DD / return for the full run and both halves
("SWING_QC ..."), so one run per mode is enough. Without project parameters, edit
DEFAULT_MODE / DEFAULT_UNIVERSE below.
"""

from collections import deque
from datetime import date

DEFAULT_MODE = "sleeve"       # "sleeve" or "risk"
DEFAULT_UNIVERSE = "majors"   # "majors" or "alts"
SPLIT = date(2023, 9, 1)

COST_SIDE = 0.001
SWAP_YEAR = 0.20
N_ENTRY, N_EXIT, N_REGIME, N_MOM = 20, 10, 200, 90
BARS = max(N_REGIME, N_ENTRY + 1, N_EXIT + 1, N_MOM + 1)

MAJORS = ["BTCUSD", "ETHUSD"]
ALTS = ["BTCUSD", "ETHUSD", "LTCUSD", "BCHUSD", "ETCUSD", "XLMUSD", "ZECUSD", "LINKUSD", "ADAUSD", "SOLUSD",
        "DOGEUSD", "DOTUSD", "UNIUSD", "AAVEUSD", "ATOMUSD", "AVAXUSD", "ALGOUSD", "FILUSD", "XTZUSD", "MANAUSD",
        "SANDUSD", "AXSUSD", "CHZUSD", "CRVUSD", "COMPUSD", "MKRUSD", "SNXUSD", "YFIUSD", "BATUSD", "GRTUSD",
        "SUSHIUSD", "1INCHUSD", "ENJUSD", "KSMUSD", "IMXUSD", "APEUSD", "NEARUSD", "ICPUSD", "XRPUSD", "LRCUSD"]


class PctFee(FeeModel):
    def get_order_fee(self, parameters):
        o, s = parameters.order, parameters.security
        return OrderFee(CashAmount(abs(o.absolute_quantity) * s.price * COST_SIDE, "USD"))


class SwingDonchianQC(QCAlgorithm):
    def initialize(self):
        self.mode = self.get_parameter("mode", DEFAULT_MODE)
        uni = self.get_parameter("universe", DEFAULT_UNIVERSE)
        self.label = f"{self.mode}+{uni}"
        self.risk_frac = float(self.get_parameter("risk_frac", "0.005"))
        self.max_pos = int(self.get_parameter("max_positions", "10"))
        y0, m0, d0 = (int(x) for x in self.get_parameter("start", "2018-03-05").split("-"))
        y1, m1, d1 = (int(x) for x in self.get_parameter("end", "2026-09-30").split("-"))
        self.set_start_date(y0, m0, d0)
        self.set_end_date(y1, m1, d1)
        self.set_cash(10000)
        self.set_brokerage_model(BrokerageName.COINBASE, AccountType.CASH)
        self.add_security_initializer(self._init_security)

        self.hist = {}
        for t in (MAJORS if uni == "majors" else ALTS):
            try:
                sym = self.add_crypto(t, Resolution.DAILY, Market.COINBASE).symbol
            except Exception as e:
                self.debug(f"skip {t}: {e}")
                continue
            self.hist[sym] = (deque(maxlen=BARS), deque(maxlen=BARS), deque(maxlen=BARS))
        self.n_slots = len(self.hist)
        self.set_warm_up(BARS + 5, Resolution.DAILY)
        self.equity_curve = []

    def _init_security(self, security):
        security.set_fee_model(PctFee())
        security.set_slippage_model(NullSlippageModel())

    def _levels(self, sym):
        h, l, c = self.hist[sym]
        if len(c) < BARS:
            return None
        hs, ls, cs = list(h), list(l), list(c)
        close = cs[-1]
        sma = sum(cs[-N_REGIME:]) / N_REGIME
        entry_high = max(hs[-N_ENTRY - 1:-1])
        exit_low = min(ls[-N_EXIT - 1:-1])
        base = cs[-N_MOM - 1]
        return close, sma, entry_high, exit_low, (close / base - 1) if base > 0 else 0.0

    def on_data(self, data: Slice):
        for sym, (h, l, c) in self.hist.items():
            bar = (data.bars[sym] if data.bars.contains_key(sym) else None)
            if bar is not None and bar.close > 0:
                h.append(float(bar.high))
                l.append(float(bar.low))
                c.append(float(bar.close))
        if self.is_warming_up:
            return
        self._charge_swap()
        self.equity_curve.append((self.time.date(), float(self.portfolio.total_portfolio_value)))

        lv = {s: self._levels(s) for s in self.hist}
        held = [s for s in self.hist if self.portfolio[s].invested]
        for s in held:
            x = lv[s]
            if x is None:
                continue
            close, sma, _, exit_low, _ = x
            if close < exit_low or close < sma:
                self.liquidate(s, tag="bajo_minimo_10d" if close < exit_low else "bajo_sma200")
        still = [s for s in held if self.portfolio[s].invested and not self.transactions.get_open_orders(s)]
        exiting = set(held) - set(still)

        cands = []
        for s, x in lv.items():
            if x is None or s in held or not data.bars.contains_key(s):
                continue
            close, sma, entry_high, exit_low, mom = x
            if close > entry_high and close > sma and exit_low < close:
                cands.append((mom, s, close, exit_low))
        cands.sort(key=lambda z: -z[0])

        if self.mode == "sleeve":
            self._rebalance_sleeves(still, [s for _, s, _, _ in cands])
            return
        slots = max(0, self.max_pos - (len(held) - len(exiting)))
        equity = self.portfolio.total_portfolio_value
        for _, s, close, exit_low in cands[:slots]:
            qty = self.risk_frac * equity / (close - exit_low)
            qty = min(qty, self.portfolio.cash_book["USD"].amount * 0.98 / close)
            if qty * close >= 10:
                self.market_order(s, qty, tag="donchian_20_sma200")

    def _rebalance_sleeves(self, held, new):
        """Each 'on' symbol at 1/N of equity, like the research (mean of sleeve returns).

        Rebalance only on a new entry or past 10% drift, and then every held symbol
        together so LEAN sells the overweight one before buying (cash account).
        A 2% cash buffer covers fees and the fill-price move.
        """
        w = 0.98 / self.n_slots
        tpv = self.portfolio.total_portfolio_value
        if tpv <= 0:
            return
        drift = any(abs(self.portfolio[s].holdings_value / tpv - w) > 0.1 * w for s in held)
        if new or drift:
            self.set_holdings([PortfolioTarget(s, w) for s in list(held) + list(new)], tag="donchian_20_sma200")

    def on_end_of_algorithm(self):
        segs = {"full": self.equity_curve,
                "A_2018_2023": [x for x in self.equity_curve if x[0] < SPLIT],
                "B_2023_2026": [x for x in self.equity_curve if x[0] >= SPLIT]}
        for name, seg in segs.items():
            if len(seg) < 2 or seg[0][1] <= 0:
                self.log(f"SWING_QC {self.label} {name} sin_datos")
                continue
            years = max((seg[-1][0] - seg[0][0]).days / 365.25, 1e-9)
            total = seg[-1][1] / seg[0][1] - 1
            cagr = (1 + total) ** (1 / years) - 1
            peak, dd = seg[0][1], 0.0
            for _, v in seg:
                peak = max(peak, v)
                dd = min(dd, v / peak - 1)
            msg = f"CAGR {cagr * 100:.1f}% | DD {dd * 100:.1f}% | total {total * 100:.1f}%"
            self.log(f"SWING_QC {self.label} {name} {seg[0][0]}..{seg[-1][0]} {msg}")
            self.set_runtime_statistic(f"{name}", msg)

    def _charge_swap(self):
        notional = sum(self.portfolio[s].holdings_value for s in self.hist if self.portfolio[s].invested)
        if notional > 0:
            self.portfolio.cash_book["USD"].add_amount(-notional * SWAP_YEAR / 365.0)

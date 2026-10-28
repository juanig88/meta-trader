-- Quant data layer for the MT5 bot.
--
-- Two retention tiers:
--   ephemeral  -> md_ticks, md_bars. Range-partitioned by UTC day so the
--                 janitor can DROP whole partitions instead of DELETE-ing
--                 rows. High-frequency ticks would otherwise bloat the heap
--                 faster than autovacuum reclaims it.
--   persistent -> trade_events, positions_closed, equity_snapshots,
--                 alpha_scores, manual_trade_journal, manual_miner_reports.
--                 Never purged.
--
-- This file is idempotent: safe to re-apply against an existing database.

SET timezone = 'UTC';


-- ---------------------------------------------------------------------------
-- Ephemeral tier (TTL, partitioned by day)
-- ---------------------------------------------------------------------------

-- ts comes from MT5 time_msc. MT5 reports broker server time as a unix
-- timestamp; the ingest daemon normalises it to UTC. ts_msc keeps the raw
-- millisecond value so ordering survives the conversion.
CREATE TABLE IF NOT EXISTS md_ticks (
    symbol      text              NOT NULL,
    ts          timestamptz       NOT NULL,
    ts_msc      bigint            NOT NULL,
    bid         double precision,
    ask         double precision,
    last        double precision,
    volume      double precision,
    flags       integer,
    spread_pts  double precision
) PARTITION BY RANGE (ts);

CREATE INDEX IF NOT EXISTS md_ticks_symbol_ts_idx ON md_ticks (symbol, ts DESC);

CREATE TABLE IF NOT EXISTS md_bars (
    symbol      text              NOT NULL,
    timeframe   text              NOT NULL,
    ts          timestamptz       NOT NULL,
    open        double precision  NOT NULL,
    high        double precision  NOT NULL,
    low         double precision  NOT NULL,
    close       double precision  NOT NULL,
    tick_volume bigint,
    real_volume bigint,
    spread      integer,
    PRIMARY KEY (symbol, timeframe, ts)
) PARTITION BY RANGE (ts);


-- ---------------------------------------------------------------------------
-- Partition management
--
-- Exposed as functions so the Python janitor never builds DDL strings itself:
-- it calls SELECT md_ensure_partition(%s, %s) with bound parameters.
-- The parent whitelist keeps this from becoming an arbitrary-DDL primitive.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION md_ensure_partition(p_parent text, p_day date)
RETURNS text
LANGUAGE plpgsql
AS $fn$
DECLARE
    v_child text;
BEGIN
    IF p_parent NOT IN ('md_ticks', 'md_bars') THEN
        RAISE EXCEPTION 'md_ensure_partition: unsupported parent table %', p_parent;
    END IF;

    v_child := format('%s_%s', p_parent, to_char(p_day, 'YYYY_MM_DD'));

    EXECUTE format(
        'CREATE TABLE IF NOT EXISTS %I PARTITION OF %I FOR VALUES FROM (%L) TO (%L)',
        v_child,
        p_parent,
        (p_day::timestamp AT TIME ZONE 'UTC'),
        ((p_day + 1)::timestamp AT TIME ZONE 'UTC')
    );

    RETURN v_child;
END;
$fn$;

CREATE OR REPLACE FUNCTION md_drop_partitions_before(p_parent text, p_cutoff date)
RETURNS integer
LANGUAGE plpgsql
AS $fn$
DECLARE
    v_child   text;
    v_day     date;
    v_dropped integer := 0;
BEGIN
    IF p_parent NOT IN ('md_ticks', 'md_bars') THEN
        RAISE EXCEPTION 'md_drop_partitions_before: unsupported parent table %', p_parent;
    END IF;

    FOR v_child IN
        SELECT c.relname
        FROM pg_inherits i
        JOIN pg_class c ON c.oid = i.inhrelid
        JOIN pg_class p ON p.oid = i.inhparent
        WHERE p.relname = p_parent
    LOOP
        -- Only touch children this module named; anything else is left alone.
        IF right(v_child, 10) !~ '^\d{4}_\d{2}_\d{2}$' THEN
            CONTINUE;
        END IF;

        v_day := to_date(right(v_child, 10), 'YYYY_MM_DD');
        IF v_day < p_cutoff THEN
            EXECUTE format('DROP TABLE IF EXISTS %I', v_child);
            v_dropped := v_dropped + 1;
        END IF;
    END LOOP;

    RETURN v_dropped;
END;
$fn$;

-- Inventory view used by the janitor logs and the verification step.
CREATE OR REPLACE VIEW md_partition_inventory AS
SELECT
    p.relname                                   AS parent,
    c.relname                                   AS child,
    to_date(right(c.relname, 10), 'YYYY_MM_DD') AS day,
    pg_total_relation_size(c.oid)               AS bytes
FROM pg_inherits i
JOIN pg_class c ON c.oid = i.inhrelid
JOIN pg_class p ON p.oid = i.inhparent
WHERE p.relname IN ('md_ticks', 'md_bars')
  AND right(c.relname, 10) ~ '^\d{4}_\d{2}_\d{2}$';


-- ---------------------------------------------------------------------------
-- Persistent tier (never purged)
-- ---------------------------------------------------------------------------

-- Mirrors the JSON payloads bot_mt5._log_event writes to trades.log.
-- trades.log stays authoritative; this table exists for querying.
CREATE TABLE IF NOT EXISTS trade_events (
    id      bigserial    PRIMARY KEY,
    ts      timestamptz  NOT NULL DEFAULT now(),
    event   text         NOT NULL,
    symbol  text,
    ticket  bigint,
    magic   bigint,
    payload jsonb        NOT NULL
);

CREATE INDEX IF NOT EXISTS trade_events_ts_idx     ON trade_events (ts DESC);
CREATE INDEX IF NOT EXISTS trade_events_event_idx  ON trade_events (event, ts DESC);
CREATE INDEX IF NOT EXISTS trade_events_symbol_idx ON trade_events (symbol, ts DESC);
CREATE INDEX IF NOT EXISTS trade_events_ticket_idx ON trade_events (ticket) WHERE ticket IS NOT NULL;

CREATE TABLE IF NOT EXISTS positions_closed (
    ticket      bigint       PRIMARY KEY,
    symbol      text         NOT NULL,
    side        text         NOT NULL,
    volume      double precision,
    price_open  double precision,
    price_close double precision,
    sl          double precision,
    tp          double precision,
    opened_at   timestamptz,
    closed_at   timestamptz,
    profit      double precision,
    commission  double precision,
    swap        double precision,
    net_profit  double precision,
    magic       bigint,
    comment     text,
    payload     jsonb
);

CREATE INDEX IF NOT EXISTS positions_closed_closed_at_idx ON positions_closed (closed_at DESC);
CREATE INDEX IF NOT EXISTS positions_closed_symbol_idx    ON positions_closed (symbol, closed_at DESC);

CREATE TABLE IF NOT EXISTS equity_snapshots (
    id             bigserial   PRIMARY KEY,
    ts             timestamptz NOT NULL DEFAULT now(),
    balance        double precision,
    equity         double precision,
    margin         double precision,
    free_margin    double precision,
    margin_level   double precision,
    open_positions integer,
    floating_pnl   double precision
);

CREATE INDEX IF NOT EXISTS equity_snapshots_ts_idx ON equity_snapshots (ts DESC);

-- Shadow-mode alpha engine output. Nothing reads this to place orders.
CREATE TABLE IF NOT EXISTS alpha_scores (
    id              bigserial    PRIMARY KEY,
    ts              timestamptz  NOT NULL DEFAULT now(),
    symbol          text         NOT NULL,
    timeframe       text         NOT NULL,
    direction       text         NOT NULL,
    score           double precision NOT NULL,
    momentum        double precision,
    volatility      double precision,
    zscore          double precision,
    flow            double precision,
    gross_edge      double precision,
    cost_spread     double precision,
    cost_commission double precision,
    cost_slippage   double precision,
    net_edge        double precision,
    features        jsonb
);

CREATE INDEX IF NOT EXISTS alpha_scores_symbol_ts_idx ON alpha_scores (symbol, ts DESC);
CREATE INDEX IF NOT EXISTS alpha_scores_net_edge_idx  ON alpha_scores (net_edge DESC, ts DESC);

-- Closed discretionary trades (magic 0). Written by the manual pattern miner.
-- Not read by the live order path. Context columns stay null until bars exist;
-- a later pass fills them without overwriting a snapshot that already has a zscore.
CREATE TABLE IF NOT EXISTS manual_trade_journal (
    position_id         bigint PRIMARY KEY,
    close_deal          bigint,
    symbol              text              NOT NULL,
    side                text              NOT NULL,
    magic               bigint,
    comment             text,
    volume              double precision,
    price_open          double precision,
    price_close         double precision,
    opened_at           timestamptz,
    closed_at           timestamptz,
    net_profit          double precision,
    winner              boolean           NOT NULL DEFAULT false,
    hour_of_day         smallint,
    correlation_groups  text,
    zscore              double precision,
    rsi                 double precision,
    rsi_velocity        double precision,
    atr                 double precision,
    bandwidth           double precision,
    squeeze             boolean,
    rvol                double precision,
    alpha_direction     text,
    alpha_score         double precision,
    q                   double precision,
    dashboard           jsonb,
    context             jsonb,
    created_at          timestamptz       NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS manual_trade_journal_closed_at_idx
    ON manual_trade_journal (closed_at DESC);
CREATE INDEX IF NOT EXISTS manual_trade_journal_winner_idx
    ON manual_trade_journal (winner, closed_at DESC);

-- Analytical snapshots from the host-side manual miner daemon.
-- Written when the journal grows by REPORT_EVERY rows, and once when
-- winners cross STAT_MIN. Not read by the live order path. Not purged.
CREATE TABLE IF NOT EXISTS manual_miner_reports (
    id          bigserial   PRIMARY KEY,
    ts          timestamptz NOT NULL DEFAULT now(),
    trigger     text        NOT NULL,
    n_trades    integer     NOT NULL,
    n_winners   integer     NOT NULL,
    n_new       integer     NOT NULL,
    summary     text        NOT NULL,
    comparison  jsonb
);

CREATE INDEX IF NOT EXISTS manual_miner_reports_ts_idx
    ON manual_miner_reports (ts DESC);

-- Ensemble rotation votes. Written by the bot; Lab reads latest per symbol.
-- Not used to place orders. CREATE also runs on first persist if the volume predates this.
CREATE TABLE IF NOT EXISTS ensemble_votes (
    id              bigserial    PRIMARY KEY,
    ts              timestamptz  NOT NULL DEFAULT now(),
    symbol          text         NOT NULL,
    direction       text         NOT NULL,
    buy_count       integer      NOT NULL,
    sell_count      integer      NOT NULL,
    none_count      integer      NOT NULL,
    fired           boolean      NOT NULL,
    action          text         NOT NULL,
    risk_usd        double precision,
    lot             double precision,
    skip_reason     text,
    dry_run         boolean      NOT NULL DEFAULT true,
    votes           jsonb        NOT NULL,
    murphy          jsonb
);

CREATE INDEX IF NOT EXISTS ensemble_votes_symbol_ts_idx ON ensemble_votes (symbol, ts DESC);
CREATE INDEX IF NOT EXISTS ensemble_votes_ts_idx ON ensemble_votes (ts DESC);

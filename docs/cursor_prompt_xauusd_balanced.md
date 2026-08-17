# Prompt para Cursor: Implementación de Scalping XAUUSD (Opción Equilibrada 1:1)

**Contexto:**
Este es un bot de trading algorítmico en Python (`bot_mt5.py`). El objetivo es actualizar la estrategia paralela de XAUUSD para que haga Scalping en M15 (cruce de EMAs 9 y 21 + ADX > 20), enviando órdenes con Stop Loss y Take Profit físicos al broker. 
Basado en análisis de riesgo/beneficio para Oro, se utilizará una **opción equilibrada 1:1** (`TP = $0.50` y `SL = $0.50`) para evitar desbalances matemáticos. Todo debe ser parametrizable desde el archivo `.env`. Además, se debe actualizar el dashboard (Flask).

Por favor, aplica las siguientes modificaciones paso a paso:

---

### Tarea 1: Archivo `.env` (o `.env.example`)
Actualiza o agrega estas variables en la sección de `XAUUSD paralelo` con los valores equilibrados:
```ini
BOT_XAUUSD_PARALLEL_SL_MONEY=0.50
BOT_XAUUSD_PARALLEL_TP_MONEY=0.50
BOT_XAUUSD_PARALLEL_START_HOUR=10
BOT_XAUUSD_PARALLEL_END_HOUR=14
```

---

### Tarea 2: Actualizar `bot_mt5.py` (Lógica y Configuración)

**1. Actualizar la dataclass `Config`:**
Busca `class Config:` y en la sección de variables de `XAUUSD parallel strategy`, agrega:
```python
    xauusd_parallel_sl_money: float = 0.50
    xauusd_parallel_start_hour: int = 10
    xauusd_parallel_end_hour: int = 14
```

**2. Actualizar el parseo en `main()`:**
Busca la inicialización `cfg = Config(` al final del archivo y agrega las nuevas variables de configuración:
```python
        xauusd_parallel_sl_money=float(_env("BOT_XAUUSD_PARALLEL_SL_MONEY", "0.50")),
        xauusd_parallel_start_hour=int(float(_env("BOT_XAUUSD_PARALLEL_START_HOUR", "10"))),
        xauusd_parallel_end_hour=int(float(_env("BOT_XAUUSD_PARALLEL_END_HOUR", "14"))),
```

**3. Nueva Señal de Scalping (M15 - EMAs 9/21 + ADX > 20):**
Busca la función `_xauusd_simple_market_trend` y reemplázala por:
```python
def _xauusd_scalping_signal(symbol: str) -> Signal:
    """Lógica de scalping XAUUSD M15: Cruce EMA 9/21 + ADX > 20"""
    ema_fast = get_ema(symbol, mt5.TIMEFRAME_M15, 9)
    ema_slow = get_ema(symbol, mt5.TIMEFRAME_M15, 21)
    adx_val = get_adx(symbol, mt5.TIMEFRAME_M15, 14)
    tick = mt5.symbol_info_tick(symbol)

    if ema_fast is None or ema_slow is None or adx_val is None or tick is None:
        return "NONE"

    price = float(tick.bid)

    if adx_val < 20.0:
        return "NONE"

    if ema_fast > ema_slow and price > ema_fast:
        return "BUY"
    if ema_fast < ema_slow and price < ema_fast:
        return "SELL"
    return "NONE"
```

**4. Ciclo de Ejecución con Horario de Config:**
Busca `run_xauusd_parallel_cycle(cfg: Config) -> None:` y reemplázala por:
```python
def run_xauusd_parallel_cycle(cfg: Config) -> None:
    if not bool(cfg.xauusd_parallel_enabled):
        return

    sym = _xauusd_parallel_symbol(cfg)

    # 1. Filtro Horario (Desde cfg)
    now = datetime.now()
    if not (cfg.xauusd_parallel_start_hour <= now.hour < cfg.xauusd_parallel_end_hour):
        return

    # 2. Exposición
    max_pos = max(1, int(getattr(cfg, "xauusd_parallel_max_positions", 1) or 1))
    if len(_xauusd_parallel_positions(cfg)) >= max_pos:
        return

    # 3. Señal
    decision = _xauusd_scalping_signal(sym)
    if decision == "NONE":
        return

    logging.info("[%s] XAUUSD parallel signal: Scalping M15 → %s", sym, decision)
    _log_event({
        "event": "XAUUSD_PARALLEL_SIGNAL",
        "ts": datetime.now().isoformat(timespec="seconds"),
        "symbol": sym,
        "trend_m15_scalp": decision,
        "decision": decision,
    })
    open_xauusd_parallel_trade(cfg, decision)
```

**5. SL/TP Físicos en `open_xauusd_parallel_trade` (Ratio 1:1):**
En `open_xauusd_parallel_trade`, modifica el diccionario `request` y asegúrate de leer el SL desde la configuración:
```python
    request: dict = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": sym,
        "volume": lot,
        "type": order_type,
        "price": price,
        "deviation": cfg.deviation_points,
        "magic": magic,
        "comment": "xauusd-parallel",
        "type_time": mt5.ORDER_TIME_GTC,
    }

    tp_money = float(getattr(cfg, "xauusd_parallel_tp_money", 0.50) or 0.50)
    sl_money = float(getattr(cfg, "xauusd_parallel_sl_money", 0.50) or 0.50)
    
    sl_dist = _price_distance_for_money(sym, lot, sl_money, side)
    tp_dist = _price_distance_for_money(sym, lot, tp_money, side)

    if sl_dist is not None and tp_dist is not None:
        if side == "BUY":
            request["sl"] = round(price - sl_dist, digits)
            request["tp"] = round(price + tp_dist, digits)
        else:
            request["sl"] = round(price + sl_dist, digits)
            request["tp"] = round(price - tp_dist, digits)

    filling_candidates = [
        mt5.ORDER_FILLING_IOC,
        mt5.ORDER_FILLING_FOK,
        getattr(mt5, "ORDER_FILLING_RETURN", mt5.ORDER_FILLING_IOC),
    ]

    logging.info(
        "[%s] XAUUSD parallel opening %s | lot=%.2f | price=%.*f | spread=%.1f pts | SL=%.*f | TP=%.*f",
        sym, side, lot, digits, price, spread if spread is not None else 0.0,
        digits, request.get("sl", 0.0), digits, request.get("tp", 0.0)
    )
```

---

### Tarea 3: Modificar `flask_dashboard.py`
En `def index():` (ruta `/`), extrae el estado y pásalo al template:
```python
@app.get("/")
def index():
    state = get_bot_state()
    health = compute_health(state)
    xauusd_state = state.get("xauusd_parallel", {})

    return render_template(
        "index.html",
        state=state,
        health=health,
        now=datetime.now(),
        forward_latest=_forward_latest_summary(),
        forward_ok=request.args.get("forward_ok"),
        forward_err=request.args.get("forward_err", ""),
        forward_rc=request.args.get("forward_rc", type=int),
        telegram_test=request.args.get("telegram_test", ""),
        telegram_test_msg=request.args.get("telegram_test_msg", ""),
        regime_export_text=_market_regime_export_text(state),
        xauusd_state=xauusd_state,
    )
```

---

### Tarea 4: Modificar `bot/templates/index.html`
Agrega la tarjeta de XAUUSD en la grilla principal (debajo del Estado del bot):
```html
    {% if xauusd_state.get('enabled') %}
    <div class="card">
      <h2>⚡ Scalper XAUUSD (M15)</h2>
      <div class="kv">
        <div class="muted">Símbolo</div><div>{{ xauusd_state.get('symbol', 'XAUUSD') }}</div>
        <div class="muted">Posiciones Abiertas</div>
        <div style="color:{% if xauusd_state.get('open_positions', 0) > 0 %}#f59e0b{% else %}#94a3b8{% endif %}">
          {{ xauusd_state.get('open_positions', 0) }} / {{ xauusd_state.get('max_positions', 1) }}
        </div>
        <div class="muted">Target / Stop (1:1)</div><div style="color:#16a34a">${{ xauusd_state.get('tp_money', 0.50) }}</div>
        <div class="muted">Estado Horario</div>
        {% set current_hour = now.hour %}
        {% if current_hour >= 10 and current_hour < 14 %}
          <div style="color:#16a34a">Activo (Operando)</div>
        {% else %}
          <div style="color:#dc2626">Fuera de horario (10-14hs)</div>
        {% endif %}
      </div>
    </div>
    {% endif %}
```

# Prompt para Cursor: Implementación de Estrategia Scalping XAUUSD

**Contexto del proyecto:**
Este es un bot de trading algorítmico para MetaTrader 5 escrito en Python (`bot_mt5_2.py`). El bot ya posee una arquitectura para operar múltiples símbolos y tiene una estructura separada para ejecutar una estrategia paralela y aislada en XAUUSD (usando su propio `magic number`).

**Objetivo:**
Modificar la lógica paralela actual de XAUUSD (que actualmente usa H4) para implementar una estrategia de **Scalping en M15**. Se debe agregar un filtro de horario estricto, señales basadas en cruce de EMAs rápidas con ADX, y asegurar que las órdenes se envíen con Stop Loss y Take Profit físicos al broker. Además, se debe actualizar el dashboard web (Flask) para reflejar el estado de esta estrategia.

Por favor, aplica las siguientes modificaciones en los archivos indicados:

---

### Tarea 1: Modificar `bot_mt5_2.py`

**1. Reemplazar la función de tendencia simple por la nueva señal de scalping:**
Busca la función `_xauusd_simple_market_trend(symbol: str) -> Trend:` y **reemplázala completamente** por esta nueva función:

```python
def _xauusd_scalping_signal(symbol: str) -> Signal:
    """
    Lógica de scalping para XAUUSD en M15.
    Busca cruce de EMAs rápidas (9, 21) con confirmación de momentum (ADX > 20).
    """
    ema_fast = get_ema(symbol, mt5.TIMEFRAME_M15, 9)
    ema_slow = get_ema(symbol, mt5.TIMEFRAME_M15, 21)
    adx_val = get_adx(symbol, mt5.TIMEFRAME_M15, 14)
    tick = mt5.symbol_info_tick(symbol)

    if ema_fast is None or ema_slow is None or adx_val is None or tick is None:
        return "NONE"

    price = float(tick.bid)

    # Filtro de fuerza: no entramos si el mercado está planchado
    if adx_val < 20.0:
        return "NONE"

    # Señal de Compra
    if ema_fast > ema_slow and price > ema_fast:
        return "BUY"
    
    # Señal de Venta
    if ema_fast < ema_slow and price < ema_fast:
        return "SELL"

    return "NONE"
```

**2. Actualizar el ciclo de ejecución para usar horario y la nueva señal:**
Busca la función `run_xauusd_parallel_cycle(cfg: Config) -> None:` y modifícala para que quede exactamente así (prestando atención al filtro de horario de 10 a 14hs):

```python
def run_xauusd_parallel_cycle(cfg: Config) -> None:
    """
    Ciclo de entrada XAUUSD paralelo:
    - Valida ventana horaria (10 a 14 hs).
    - Valida límite de posiciones.
    - Ejecuta señal M15.
    """
    if not bool(cfg.xauusd_parallel_enabled):
        return

    sym = _xauusd_parallel_symbol(cfg)

    # 1. FILTRO DE HORARIO (10:00 a 14:00 local del bot)
    now = datetime.now()
    if not (10 <= now.hour < 14):
        return

    # 2. FILTRO DE EXPOSICIÓN
    max_pos = max(1, int(getattr(cfg, "xauusd_parallel_max_positions", 1) or 1))
    if len(_xauusd_parallel_positions(cfg)) >= max_pos:
        return

    # 3. EVALUACIÓN DE SEÑAL SCALPING
    decision = _xauusd_scalping_signal(sym)

    if decision == "NONE":
        return

    logging.info("[%s] XAUUSD parallel signal: Scalping M15 → %s", sym, decision)
    _log_event(
        {
            "event": "XAUUSD_PARALLEL_SIGNAL",
            "ts": datetime.now().isoformat(timespec="seconds"),
            "symbol": sym,
            "trend_m15_scalp": decision,
            "decision": decision,
        }
    )
    open_xauusd_parallel_trade(cfg, decision)
```

**3. Agregar SL/TP físicos en la creación de la orden:**
Busca la función `open_xauusd_parallel_trade(cfg: Config, side: Signal) -> bool:`. Dentro de esa función, localiza la definición del diccionario `request` y modifícalo para calcular e inyectar el SL y TP físicos antes de definir `filling_candidates`. Debe quedar así:

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

    # --- NUEVO: SL y TP FÍSICOS AL BROKER ---
    tp_money = float(getattr(cfg, "xauusd_parallel_tp_money", 0.25) or 0.25)
    sl_money = 1.0  # Límite de pérdida dura para XAUUSD
    
    sl_dist = _price_distance_for_money(sym, lot, sl_money, side)
    tp_dist = _price_distance_for_money(sym, lot, tp_money, side)

    if sl_dist is not None and tp_dist is not None:
        if side == "BUY":
            request["sl"] = round(price - sl_dist, digits)
            request["tp"] = round(price + tp_dist, digits)
        else:
            request["sl"] = round(price + sl_dist, digits)
            request["tp"] = round(price - tp_dist, digits)
    # ----------------------------------------
```
Y justo debajo, actualiza el `logging.info` de la apertura para que muestre el SL y TP:
```python
    logging.info(
        "[%s] XAUUSD parallel opening %s | lot=%.2f | price=%.*f | spread=%.1f pts | SL=%.*f | TP=%.*f",
        sym, side, lot, digits, price, spread if spread is not None else 0.0,
        digits, request.get("sl", 0.0), digits, request.get("tp", 0.0)
    )
```

---

### Tarea 2: Modificar `flask_dashboard.py`

Localiza la ruta `@app.get("/")` y la función `index()`.
Agrega la extracción del estado de `xauusd_parallel` y pásalo al contexto del template. Debe quedar así:

```python
@app.get("/")
def index():
    state = get_bot_state()
    health = compute_health(state)
    
    # NUEVO: Extraemos la data de XAUUSD Paralelo si existe
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
        xauusd_state=xauusd_state, # Pasamos la data al HTML
    )
```

---

### Tarea 3: Modificar `bot/templates/index.html`

Busca la grilla principal de tarjetas (generalmente un `<div class="grid">`) e inyecta la siguiente tarjeta para XAUUSD usando Jinja2. Puedes ponerla debajo de la tarjeta "🤖 Estado del bot":

```html
    {% if xauusd_state.get('enabled') %}
    <div class="card">
      <h2>⚡ Scalper XAUUSD (M15)</h2>
      <div class="kv">
        <div class="muted">Símbolo</div><div>{{ xauusd_state.get('symbol', 'XAUUSD') }}</div>
        <div class="muted">Magic Number</div><div>{{ xauusd_state.get('magic', 'N/A') }}</div>
        <div class="muted">Posiciones Abiertas</div>
        <div style="color:{% if xauusd_state.get('open_positions', 0) > 0 %}#f59e0b{% else %}#94a3b8{% endif %}">
          {{ xauusd_state.get('open_positions', 0) }} / {{ xauusd_state.get('max_positions', 1) }}
        </div>
        <div class="muted">Take Profit (USD)</div><div>${{ xauusd_state.get('tp_money', 0) }}</div>
        <div class="muted">Estado Horario</div>
        {% set current_hour = now.hour %}
        {% if current_hour >= 10 and current_hour < 14 %}
          <div style="color:#16a34a">Activo (Operando)</div>
        {% else %}
          <div style="color:#dc2626">Fuera de horario</div>
        {% endif %}
      </div>
    </div>
    {% endif %}
```

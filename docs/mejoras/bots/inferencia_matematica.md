# Inferencia matemática — señales MQL5 en `docs/mejoras/bots`

Análisis sobre screenshots + CSV de posiciones (`_analyze_signals.py`).  
IDs = signal MQL5: `2234595`, `2362973`, `2381521`.

---

## Resumen ejecutivo

| Signal | ¿“Buena” estrategia? | Familia inferida | Copiable sin grid? |
|---|---|---|---|
| **2234595** | Equity linda, **riesgo de grid/martingale** | Grid tendencia + bias BUY XAU, entradas M15 | Parcial (filtro sesión/M15 sí; grid no) |
| **2362973** | Edge matemático frágil (WR≈90%, RR pésimo) | Scalp ultra-rápido, **SL fijo −10$ precio**, 1 pos | Sí (reglas simples) |
| **2381521** | Marginal (PF 1.24) | Scalp TP≈+2.2 / SL≈−12, lot fijo 0.01 | Sí |

Ninguna es “sagrada”. Las tres se apoyan en **alta WR + pérdida media > ganancia media**. Eso funciona hasta que la cola de pérdidas o el grid se come la cuenta.

Fórmula común:

\[
E = p \cdot W - (1-p) \cdot L
\]

Si \(L \gg W\), necesitás \(p\) muy alto. Un leve drop de winrate destruye el edge.

---

## 2234595 — casi seguro Quantum-style grid

**Stats UI:** 1443 trades, WR 77%, PF 2.60, hold ~1h, 31/sem, equity DD **31%** vs balance DD ~11–17%.

**Del CSV (más fuerte que el marketing):**

1. **Grid / recovery confirmado**
   - 280 clusters con **mismo timestamp de cierre**
   - Máx **16 posiciones concurrentes**
   - Cluster 2026-07-29: 16× BUY escalonados a la baja (`4100 → 4053`), lots `0.04 → 0.07 → 0.11`, cierran juntos (unos en −$133, últimos en +$148). Eso es **promediar + martingale de lote**, no 1 trade = 1R.

2. **Entradas ancladas a M15**
   - Minutos de entry top: `:00` (191), `:30` (144), `:45`, `:15` → abre en bordes de vela M15.

3. **Bias direccional**
   - Buy 1220 / Sell 223; PnL BUY ≫ SELL → montado en **tendencia alcista del oro**.

4. **Edge numérico**
   - \(E \approx 0.778\cdot 6.79 - 0.222\cdot 8.30 \approx +3.44\$\) por trade @ lots chicos.
   - El DD de equity del 31% es el costo del grid flotante.

**Inferencia útil para nosotros:** sesión + señales en cierre/apertura M15 + bias H4/H1.  
**No copiar:** adds ilimitados / lot multiplier.

---

## 2362973 — scalp de micro-segundos con SL fijo

**Stats UI:** 79 trades, WR ~90%, hold **3 min** (UI) / CSV med **2 s**, 6/sem, PF 2.52.

**Del CSV:**

1. **SL matemáticamente fijo**
   - Las **8 pérdidas** tienen move exacto **−10.00** (precio XAU).
   - No es ATR variable: es **stop a 10 unidades de precio**.

2. **TP variable / runner corto**
   - Wins dispersos (0.5–8+); no hay un único TP fijo tan claro como el SL.
   - 76% de holds ≤ 10s → captura spike / momentum inmediato.

3. **1 posición a la vez**, lot grande (2.9–7.6) con autolot; no grid.

4. **Edge**
   - \(E = 0.899\cdot 827 - 0.101\cdot 2738 \approx +466\) (EUR, lot enorme).
   - Ratio avgW/avgL ≈ **0.30** → si WR cae de 90% → 80%, el edge se vaporiza:

\[
E_{80\%} = 0.8\cdot 827 - 0.2\cdot 2738 \approx -213 \quad \text{(negativo)}
\]

**Inferencia útil:** filtro muy estricto + hard SL en precio + salir en segundos si no paga.  
**Cuidado:** parece dependiente de ejecución/spread; en demo IC puede no replicar.

---

## 2381521 — scalp clásico “muchas ops, TP chico, SL 5×”

**Stats UI:** 468 trades, WR 82%, PF **1.24**, ~92/sem, hold ~59 min, DD balance max marcado alto.

**Del CSV:**

1. **TP / SL casi fijos en precio**
   - Wins se agrupan en moves **+2.2 … +2.4**
   - Losses se agrupan en **≈ −12.0 … −12.5**
   - Lot siempre **0.01** → premisa cercana a la nuestra, pero RR ~1:5.

2. **Pocas stacks** (máx 7 concurrentes; solo 12 cierres dobles) → no es grid pesado como 2234595.

3. **Buy carga el PnL; Sell pierde en neto** → otra vez sesgo alcista oro.

4. **Edge fino**
   - \(E \approx 0.56\$\)/trade. Con PF 1.24, comisión/slippage real lo puede matar.
   - Lunes/jueves negativos en el sample de DOW del CSV.

**Inferencia útil:** target ~2–2.5$ / stop ~12$ en XAU @0.01, alta frecuencia, filtro horario (hour 15 UTC destaca).  
**Cuidado:** no es “estratégia robusta”; es edge delgado.

---

## ¿Hay buenas estrategias?

| Criterio | 2234595 | 2362973 | 2381521 |
|---|---|---|---|
| Equity / marketing | Fuerte | Fuerte | OK |
| Robustez matemática | Baja (grid) | Baja (cola de −10) | Baja–media (PF 1.24) |
| Ideas reutilizables | M15 timing, bias, sesión | SL fijo estricto, selectividad | TP/SL fijos, alta freq @0.01 |
| Para nuestro bot demo | **No clonar grid** | Lab de spike filter | Candidato A/B vs nuestro scalp |

**Conclusión risk-quant:** hay **buenas pistas estructurales**, no “buenas estrategias listas para copiar”. Lo comprobablemente transferable:

1. Anclar entradas a **M15** (y/o sesión Lon/NY).  
2. Sistemas de **alta WR / SL ancho** solo con kill-switch y sin martingale.  
3. Separar tracks: (a) 1-posición SL duro, (b) session sweep, (c) nuestro ATR RR2 — **nunca mezclar con grid**.

Script: `_analyze_signals.py`.

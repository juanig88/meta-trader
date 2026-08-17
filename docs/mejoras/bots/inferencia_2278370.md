# Inferencia — señal multi-instrumento `2278370`

Fuente: `2278370.png` + `2278370.positions.csv` (`_analyze_2278370.py`).

## Qué dice el dashboard

| Métrica | Valor | Lectura |
|---|---|---|
| Trades | ~2338 (CSV útil ~2130) | Alto volumen |
| WR | ~57% | Normal (no es scalp 80%+) |
| PF | ~1.58–1.65 | OK en papel |
| Hold medio | **5 días** (mediana CSV ~4.4h; media ~69h por colas largas) | Swing / carry, no scalp |
| Activity | **98%** | Casi siempre en mercado |
| Equity DD | **70.6%** | Descalificante para clonar |
| Long bias | ~69% | Libro risk-on |
| Algo | 0% en UI | Puede ser copy/manual mix |

## Lo que el CSV demuestra

### 1. “Muchos instrumentos” ≠ diversificación real

PnL por símbolo (aprox.):

| Símbolo | n | PF | PnL | Nota |
|---|---:|---:|---:|---|
| **BTCUSD** | 1008 | 1.92 | **+10.3M** | Motor del sistema; 73% BUY |
| **XAUUSD** | 228 | 2.94 | **+7.2M** | Buen aporte |
| **USDJPY** | 243 | 1.92 | +3.4M | Bias BUY fuerte |
| **JP225 / DE30 / EURJPY…** | varios | alto | + | Satélites JPY/índice |
| **US500** | 184 | **0.73** | **−1.8M** | Destruye |
| **EURUSD** | 27 | **0.21** | **−1.9M** | Destruye |
| **USOIL** | 20 | **0.17** | **−2.6M** | Destruye |

Sin BTC+XAU el cuento cambia. Varios símbolos son **ruido o hedges malos**.

### 2. Estilo: hold the loser

- Hasta **41** posiciones concurrentes.
- Pérdidas abiertas **meses**: US500 −2.3M a 339 días; BTC −1.3M a 277 días.
- Eso explica equity DD 70% vs balance DD ~20–27%: cierran winners, dejan flotar losers.

### 3. Direccionalidad

- PnL **BUY +26M / SELL −2.3M** → el edge es **largos en tendencia** (BTC/risk-on), no un motor simétrico multi-asset.
- Si el régimen crypto/equities gira, este libro duele.

### 4. Matemática

\[
E \approx +11k\ \text{JPY/trade},\quad |W/L|\approx 1.20,\quad p\approx 0.58
\]

Más “sano” que los scalps WR90%/RR0.3, pero el **path de DD** lo mata como plantilla.

## ¿Sirve para nosotros?

| Idea | ¿Llevar? |
|---|---|
| Basket multi-símbolo en paralelo | Solo si cada uno tiene edge **propio** y kill-switch; no copiar este libro |
| BTC + XAU como pilares | Parcialmente alineado con nuestro research (con filtros) |
| Mantener perdedoras semanas/meses | **No** |
| US500/EURUSD/USOIL “porque diversifica” | **No** — acá restan |
| 40+ posiciones concurrentes | **No** |

**Veredicto:** interesante como **caso de estudio de falso portfolio**. No es una estrategia multi-instrumento limpia para dejar encendida. Es un libro BTC-long + satélites, con survivors que maquillan equity y colas de DD brutales.

Para el bot: seguir con **pocos tracks, 1 pos/símbolo, time-stop/DD kill**, no este modelo.

# Inventario: MCP y Agent Skills

Fecha: 2026-09-30. Ámbito: workspace **meta-trader**, Cursor de este usuario y skills de diseño en `~/.agents/skills/`.

**Live del repo (desde 2026-10-07):** swing diario Donchian 20/10 + SMA200 en demo XBTFX (`swing_daily.py`). Runbook: [`trading/docs/SWING_ENGINE.md`](trading/docs/SWING_ENGINE.md). La rotación está apagada. El MCP `local-trading` solo lee `bot_state.json` / `trades.log` de la rotación; no envía órdenes ni ve el swing todavía. QuantConnect se usó por el IDE web gratis (el MCP del extension pide plan pago).

Estado visto en Cursor al generar este archivo: **Connected 5** (punto verde = el agente puede llamar esas tools).

---

## Cómo leer esto

| Tipo | Qué es | Dónde vive |
|------|--------|------------|
| **MCP** | Servidor de tools que Cursor arranca (stdio/HTTP) | `.cursor/mcp.json` (proyecto) o `~/.cursor/mcp.json` (usuario) |
| **Skill** | Instrucciones `SKILL.md` que el agente carga según la descripción | Carpetas `skills/` |
| **Rule** | Directivas de rol (no son skills) | `.cursor/rules/*.mdc` |

---

## 1. MCP — proyecto `meta-trader`

Config: [`.cursor/mcp.json`](.cursor/mcp.json). En el panel aparecen con la etiqueta **meta-trader**.

| Nombre | Tools (panel) | Rol | Notas |
|--------|---------------|-----|--------|
| **ccxt** | 32 | Datos de mercado crypto (oficial `ccxt-mcp`) | Launcher: `trading/infrastructure/mcp/run_ccxt_mcp.py`. Trading forzado off. Credenciales opcionales `CCXT_MCP_*` en `.env`, nunca en `mcp.json`. |
| **local-trading** | 3 | Estado del bot MT5 local | `get_bot_snapshot`, `get_eval_tracks`, `get_recent_eval_events`. Solo lectura; no envía órdenes. |
| **vibe-trading** | 54 | Research quant: datos, backtest, factores, swarm | Comando `vibe-trading-mcp`. Lista de tools en la sección 1.1. |
| **context7** | 2 | Docs de librerías | `resolve-library-id`, `query-docs`. Duplicado con el Context7 de usuario (ver abajo). |

### 1.1 Tools de `local-trading`

- `get_bot_snapshot` — `bot_state.json` + health (secretos redactados). En rotación incluye `ensemble` (5 módulos, `min_agree` 4, skip counts).
- `get_eval_tracks` — tracks de evaluación (legado H4; con rotación ON suelen estar vacíos)
- `get_recent_eval_events` — cola de `trades.log` (`EVAL_*`). El motor vivo loguea `ENSEMBLE_VOTE` / `TRADE_OPEN` (`why_open=consenso_4_5`) / `TRADE_CLOSE` — leé el archivo, no solo esta tool.

### 1.2 Tools de `vibe-trading` (54)

Fuente: [`skills/vibe-trading/SKILL.md`](skills/vibe-trading/SKILL.md). `None*` = A-shares pueden pedir `TUSHARE_TOKEN`; HK/US/crypto suelen ir sin key.

| Tool | Para qué |
|------|----------|
| `list_skills` | Listar 86 finance skills internas |
| `load_skill` | Cargar metodología de una skill |
| `start_research_goal` / `get_research_goal` / `add_goal_evidence` / `update_research_goal_status` | Goals de research auditables |
| `backtest` | Motor de backtest vectorizado |
| `factor_analysis` | IC/IR + backtest por capas |
| `analyze_options` | Black-Scholes + Greeks |
| `pattern_recognition` | Patrones de gráfico |
| `get_market_data` | OHLCV (20 fuentes, fallback) |
| `get_fund_flow` | Flujo de fondos |
| `get_dragon_tiger` | 龙虎榜 |
| `get_northbound_flow` | Stock Connect |
| `get_margin_trading` | Margen / short |
| `get_block_trades` | Block trades |
| `get_shareholder_count` | Accionistas |
| `get_lockup_expiry` | Vencimiento de lockup |
| `get_sector_info` | Sectores |
| `get_research_reports` | Informes sell-side |
| `get_stock_news` | Noticias |
| `get_sec_filings` | SEC EDGAR |
| `get_financial_statements` | EEFF |
| `get_options_chain` | Cadena de opciones |
| `get_stock_profile` | Perfil / holdings |
| `screen_market` | Screener |
| `search_symbol` | Búsqueda de ticker |
| `get_macro_series` | FRED (pide `FRED_API_KEY`) |
| `iwencai_search` | Búsqueda A-share (pide `IWENCAI_KEY`) |
| `web_search` / `read_url` / `read_document` | Web y documentos |
| `write_file` / `read_file` | Archivos (research) |
| `analyze_trade_journal` | Journal CSV |
| `extract_shadow_strategy` / `run_shadow_backtest` / `render_shadow_report` / `scan_shadow_signals` | Shadow account |
| `list_swarm_presets` / `run_swarm` / `get_swarm_status` / `get_run_result` / `list_runs` / `reap_stale_runs` / `retry_run` | Swarms (LLM key para `run_swarm` / `retry_run`) |
| `trading_connections` / `trading_select_connection` / `trading_check` / `trading_account` / `trading_positions` / `trading_orders` / `trading_quote` / `trading_history` | Conector de broker (IBKR, etc.); no es el path de live de este repo |

**ccxt (32 tools):** mercado (exchanges, tickers, OHLCV, order book, trades, search), safety (`get_safety_status`), lecturas privadas solo si hay cuenta en env/config. En este workspace el launcher deja **trading / funds / writes en off**.

---

## 2. MCP — usuario (todas las ventanas de Cursor)

Config: `C:\Users\juani\.cursor\mcp.json`. En el panel aparecen con la etiqueta **User**.

| Nombre | Estado típico | Rol | Notas |
|--------|---------------|-----|--------|
| **context7** | Conectado (2 tools) | Mismo Context7 remoto | Segunda instancia además de la del proyecto. Podés dejar una sola para no duplicar. |
| **react-mcp** | Suele fallar en esta PC | Tools React | Apunta a `C:/Users/kalip/OneDrive/Desktop/react-mcp/index.js` (otra máquina/usuario). No sale en Connected 5. |

**QuantConnect:** no está en `mcp.json`. Lo provee la extensión Cursor “QuantConnect” (OAuth en la UI). En `AGENTS.md` es el único camino de paper/live; no estaba en el panel Connected 5 al inventariar.

---

## 3. Skills del repo (meta-trader)

Estas son las que el agente de **este workspace** debe usar para trading, arquitectura y tests.

### 3.1 `.cursor/skills/` (proyecto)

| Skill | Activación / uso |
|-------|------------------|
| **before-editing-code** | Antes de tocar código existente: analizar, no reescribir de más, diffs mínimos. |
| **clean-architecture** | Dominio sin I/O; infra → application → domain. |
| **security-checks** | Inyección, secretos, deserialización, validación de input. |
| **unit-tests-with-code** | Tests con código nuevo; table-driven; happy path + edges. |
| **trading-skill-generator** | Crear skills nuevas de TA / backtest / riesgo (Gather → Act → Verify). |

### 3.2 `skills/` (paquete vibe-trading)

| Skill | Uso |
|-------|-----|
| **vibe-trading** | Cómo usar el toolkit/CLI/MCP de Vibe-Trading (backtest, alpha zoo, journal, etc.). Complementa el MCP `vibe-trading`; no sustituye las skills de `.cursor/skills/`. |

---

## 4. Skills personales de Cursor

Ruta: `C:\Users\juani\.cursor\skills\` (todos los proyectos).

| Skill | Uso |
|-------|-----|
| **context7-mcp** | Pedir docs actuales de librerías vía Context7 (en vez de memoria del modelo). |
| **find-docs** | API/SDK/CLI/cloud docs actualizadas (misma idea; conviene no duplicar llamadas). |

---

## 5. Skills built-in de Cursor

Ruta: `C:\Users\juani\.cursor\skills-cursor\` (las mantiene Cursor; no editar ahí).

| Skill | Uso |
|-------|-----|
| **create-skill** | Autorar `SKILL.md` (formato Cursor / agentskills.io). |
| **create-rule** | Rules en `.cursor/rules/`. |
| **create-subagent** | Subagentes. |
| **create-hook** | Hooks `hooks.json`. |
| **migrate-to-skills** | Migrar rules/comandos a skills. |
| **update-cursor-settings** | `settings.json` del editor. |
| **update-cli-config** | Config del CLI de Cursor. |
| **statusline** | Barra de estado del CLI. |
| **canvas** | Artefactos analíticos en Canvas (tablas, charts, auditorías). |
| **loop** | Tareas recurrentes (`/loop`). |
| **goal** | Goals hasta completar. |
| **share** | Guardar/compartir proyecto en Cursor. |
| **origin** | CLI origin.cursor.com (auth, push). |
| **new-repo** | Repo hosted en Cursor. |
| **automate** | Cursor Automations. |
| **autopilot** | PR merge-ready (comentarios, CI). |
| **split-to-prs** | Partir trabajo en PRs chicos. |
| **review** / **review-bugbot** / **review-security** | Reviews con subagentes Bugbot / Security. |
| **sdk** | Cursor SDK (`@cursor/sdk`, `cursor-sdk`). |
| **shell** | Guía de terminal/comandos. |
| **onboard** | Onboarding del producto Cursor. |
| **rename-chat** | Renombrar el chat. |

---

## 6. Skills de diseño (Impeccable / agents)

Ruta: `C:\Users\juani\.agents\skills\`. Útiles sobre todo para UI (Gastos App), no para el bot MT5.

| Skill | Uso |
|-------|-----|
| **find-skills** | Buscar e instalar skills del ecosistema (`npx skills`). |
| **frontend-design** | UI de producción, no estética genérica. |
| **adapt** | Responsive / multi-dispositivo. |
| **animate** | Motion con propósito. |
| **audit** | Auditoría a11y / performance / theme. |
| **bolder** / **quieter** | Subir o bajar intensidad visual. |
| **clarify** | Copy, errores, microcopy. |
| **colorize** | Color estratégico. |
| **critique** | Crítica UX. |
| **delight** | Detalles memorables. |
| **distill** | Simplificar. |
| **extract** | Tokens / componentes al design system. |
| **harden** | Errores, i18n, overflow. |
| **normalize** | Alinear al design system. |
| **onboard** | Onboarding / empty states. |
| **optimize** | Performance de UI. |
| **polish** | Pase final de calidad visual. |
| **teach-impeccable** | Guardar guidelines de diseño en config. |

---

## 7. Rules del repo (no son skills)

Siempre o por glob, en [`.cursor/rules/`](.cursor/rules/):

| Rule | Rol |
|------|-----|
| `risk-quant.mdc` | Prioridad máxima en estrategias / backtests / riesgo |
| `tech-lead.mdc` | Arquitectura, MCP, deployment solo por QuantConnect |
| `dev.mdc` | Implementación bajo `trading/**/*.{ts,tsx,mjs,js}` |
| `qa.mdc` | Tests |
| `product-owner.mdc` | Docs / requisitos |

---

## 8. Cómo pedirlos en el chat

Ejemplos que disparan MCP o skills:

- “Traé OHLCV de BTC/USDT” → **ccxt** o **vibe-trading** (`get_market_data`)
- “¿El bot está vivo?” → **local-trading** (`get_bot_snapshot`)
- “¿Cómo se usa tal API de Next.js?” → **context7** / skill **find-docs**
- “Creá una skill de ATR stops” → **trading-skill-generator**

Recargar MCP: Cursor → Settings → MCP → refresh.

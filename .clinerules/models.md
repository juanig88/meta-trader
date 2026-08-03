# OpenRouter Model Configuration for Cline Roles

## Model Selection Guidelines

Since Cline doesn't automatically bind models to specific rule files, manually select the appropriate OpenRouter model before starting a task based on the role/context:

### Risk-Quant / Tech-Lead Tasks
- **Primary**: `nvidia/nemotron-3-super:free`
  - 1M context window
  - Tagged for Finance use cases
  - Benchmarked for multi-agent/agentic tool-calling workloads
- **Fallback** (if Nemotron 3 Super is rate-limited or inconsistent with MCP tools): `openai/gpt-oss-120b:free`
  - Native function calling
  - Configurable reasoning depth (set high for risk-quant tasks)
  - 131K context window

### Dev / QA Tasks
- Either of the above models works
- Prioritize whichever model is currently **not** rate-limited
- These tasks are more deterministic and less sensitive to occasional tool-calling slips

## Important Warnings

### Model Availability Changes
Several free OpenRouter models are **scheduled to be removed from the free tier on July 19, 2026**:
- Qwen3 Next 80B
- Qwen3 Coder 480B
- Venice Uncensored
- Llama 3.3 70B
- Llama 3.2 3B
- Hermes 3 405B

**Do not configure any of these as defaults for this project.**

### Free-Tier Limitations
Free-tier OpenRouter models tend to have:
- More aggressive rate limits
- Less reliable tool-calling than paid models
- Particularly inconsistent with schema-heavy MCP tools like Vibe-Trading's

**For risk-quant tasks involving strategy approval for paper/live trading:**
- Double-check critical outputs manually
- Consider switching to a paid model for that specific task if reliability is crucial

## How to Configure
1. Click the gear icon in Cline interface
2. Select "Provider" → "OpenRouter"
3. Enter your OpenRouter API key (stored securely)
4. Before starting a task, manually select the appropriate model from the dropdown based on the role guidelines above
# MCP Configuration Guide for Meta-Trader Trading Bot

This document provides comprehensive guidance on configuring and using Model Context Protocol (MCP) servers for the AI-powered quantitative trading workspace.

## Table of Contents
1. [Overview](#overview)
2. [Vibe Trading MCP Configuration](#vibe-trading-mcp-configuration)
3. [QuantConnect MCP Configuration](#quantconnect-mcp-configuration)
4. [GitHub MCP Configuration](#github-mcp-configuration)
5. [Context7 MCP Configuration](#context7-mcp-configuration)
6. [Filesystem MCP Configuration](#filesystem-mcp-configuration)
7. [Memory MCP Configuration](#memory-mcp-configuration)
8. [Sequential Thinking MCP Configuration](#sequential-thinking-mcp-configuration)
9. [Optional MCP Servers](#optional-mcp-servers)
10. [Security Guidelines](#security-guidelines)
11. [AI Role Integration](#ai-role-integration)
12. [Validation Checklist](#validation-checklist)
13. [Troubleshooting](#troubleshooting)

## Overview

Model Context Protocol (MCP) enables AI agents to interact with external tools, data sources, and services in a standardized way. This configuration establishes a comprehensive MCP ecosystem for quantitative trading development, research, and operations.

The **live MT5 bot** in this repo is the crypto **rotation** engine (5 modules, 4/5 + M15 veto, demo XBTFX). MCP here is research/read-only (vibe-trading, ccxt, local snapshot). It does not send rotation orders. Runbook: `trading/docs/ROTATION_ENGINE.md`.

Each MCP server provides specific capabilities that AI agents can leverage based on their roles and responsibilities.

## Vibe Trading MCP Configuration

### Overview
Vibe Trading MCP is the primary quantitative research tool providing market data, backtesting engines, factor analysis, and strategy validation capabilities.

### Installation Status
✅ **Already Installed**: `vibe-trading-ai` package version 0.1.11
✅ **Executable Available**: `vibe-trading-mcp` at `C:\Users\juani\AppData\Local\Python\pythoncore-3.14-64\Scripts\vibe-trading-mcp.exe`
✅ **MCP Package**: `mcp` version 1.28.1 installed

### Current Configuration
Located in `.cursor/mcp.json`:
```json
{
  "mcpServers": {
    "vibe-trading": {
      "command": "vibe-trading-mcp",
      "args": []
    }
  }
}
```

### Transport Configuration
The server supports two transport modes:
- **stdio** (default): Standard input/output for local communication
- **sse**: Server-Sent Events for network communication

For local development, stdio is recommended. For remote access, configure SSE with a specific port.

### Available Capabilities
Based on the Vibe-Trading documentation and typical MCP implementation, the server provides:

#### Market Data
- Access to 19+ data sources (equities, crypto, futures, forex)
- OHLCV data retrieval
- Reference data access
- Real-time and historical data

#### Backtesting
- Professional backtesting engines
- Walk-forward analysis
- Cost modeling (fees, slippage, spread)
- Factor zoo benchmarking (460-alpha factors)

#### Strategy Validation
- Bias detection (overfitting, lookahead, survivorship, data snooping)
- Swarm second opinions (risk_committee, quant_strategy_desk)
- Walk-forward and out-of-sample validation
- Performance metrics (Sharpe, Sortino, max drawdown, win rate, profit factor)

#### Portfolio Analytics
- Position sizing analysis
- Risk metric calculation
- Exposure analysis
- Correlation analysis

### Usage Guidelines for AI Roles

#### Risk Quant Role
- **Primary Tool**: Use for all quantitative research and validation
- **Market Data**: Pull OHLCV and reference data from verified sources
- **Backtesting**: Run strategies through professional engines with realistic cost assumptions
- **Factor Analysis**: Benchmark against 460-alpha factor zoo
- **Validation**: Require walk-forward/OOS validation with documented failure scenarios
- **Swarm Opinions**: Use risk_committee and quant_strategy_desk for high-stakes validation

#### Technical Lead / Software Architect Role
- **Validation**: Use for independent verification of strategy performance
- **Integration**: Understand available tools for potential custom adapter development
- **Research**: Explore market data capabilities for system design

### Validation Steps
1. ✅ Verify installation: `vibe-trading-mcp --help` shows usage
2. ✅ Check executable path: Available in system PATH
3. ✅ Test stdio communication: (See validation section)
4. ✅ Review configuration: Already present in `.cursor/mcp.json`

### Manual Steps Required
None - Vibe Trading MCP is already properly configured and ready for use.

## QuantConnect MCP Configuration

### Overview
QuantConnect MCP provides access to QuantConnect's cloud-based backtesting, research, and deployment platform. It serves as the secondary validation backend and deployment pipeline.

### Installation Status
⚠️ **Extension-Based**: QuantConnect MCP is provided by the QuantConnect Cursor extension
🔧 **Authentication Required**: Must be configured via the extension UI

### Configuration Location
Not configured in files - managed through Cursor extension interface.

### Setup Instructions
1. Install QuantConnect Cursor extension from VS Code marketplace
2. Authenticate via extension UI (View → Extensions → "QuantConnect")
3. Complete OAuth flow or API key configuration in extension settings
4. Verify connection status in extension panel

### Available Capabilities (when authenticated)
- **Project Scaffolding**: Create new QuantConnect projects
- **Native Backtests**: Run backtests on QC infrastructure
- **Parameter Optimization**: Genetic algorithm and grid search optimization
- **Paper/Live Deployment**: Deploy strategies to paper or live trading accounts
- **Data Access**: Access QuantConnect's extensive data library
- **Research Environment**: Access to QC's research notebooks and tools

### Usage Guidelines for AI Roles

#### Risk Quant Role
- **Secondary Validation**: Run independent backtests on QC infrastructure
- **Verification**: Confirm Vibe Trading results with QC engine
- **Requirement**: Must approve strategies only after dual-validation (Vibe + QC)

#### Technical Lead Role
- **Project Scaffolding**: Create QC projects for new strategies
- **Parameter Optimization**: Optimize strategy parameters using QC tools
- **Deployment**: Handle paper/live deployment through QC (only path for deployment)

### Validation Steps
1. ☐ Install QuantConnect Cursor extension
2. ☐ Authenticate through extension UI
3. ☐ Verify connection status shows "Connected"
4. ☐ Test basic functionality (list projects, create test project)

### Manual Steps Required
1. Install QuantConnect extension
2. Complete authentication via extension UI
3. Verify no errors in extension status

## GitHub MCP Configuration

### Overview
GitHub MCP provides programmatic access to GitHub repositories for code review, issue management, PR analysis, and repository inspection.

### Installation Status
❌ **Not Installed**: Requires installation of GitHub MCP server

### Installation Instructions
```bash
# Install via npm (recommended)
npm install -g @modelcontextprotocol/server-github

# Or via pip if Python package available
pip install mcp-server-github
```

### Configuration
After installation, add to `.cursor/mcp.json`:
```json
{
  "mcpServers": {
    "vibe-trading": {
      "command": "vibe-trading-mcp",
      "args": []
    },
    "github": {
      "command": "github-mcp",
      "args": []
    }
  }
}
```

### Authentication
GitHub MCP requires a Personal Access Token (PAT) with appropriate scopes:
- `repo`: Full control of private repositories
- `read:org`: Read organization data
- `workflow`: Update GitHub Action workflows
- `admin:repo_hook`: Manage repository hooks

**Never commit tokens to repository**. Use:
- Environment variables: `GITHUB_TOKEN`
- Secure secret management
- `.env` file (gitignored)

### Available Capabilities
- Repository contents and file operations
- Pull request creation, review, and merging
- Issue creation and management
- Commit history and blame analysis
- Branch management
- Code search
- Webhook management
- Organization and team operations

### Usage Guidelines for AI Roles

#### Developer Role
- **Code Navigation**: Explore repository structure and files
- **PR Review**: Examine pull requests before merging
- **Issue Management**: Create and update issues for tracking
- **Code Search**: Find relevant implementations and patterns

#### QA Role
- **Test Verification**: Check test files and coverage
- **Issue Tracking**: Monitor bug reports and test failures
- **Release Validation**: Verify release tags and changelogs

#### Technical Lead Role
- **Architecture Review**: Examine code structure and dependencies
- **PR Approval**: Review code changes for architectural compliance
- **Code Search**: Find patterns and anti-patterns across codebase

### Validation Steps
1. ☐ Install GitHub MCP server
2. ☐ Configure authentication via environment variable or secure store
3. ☐ Add configuration to `.cursor/mcp.json`
4. ☐ Test basic repository access

### Manual Steps Required
1. Install GitHub MCP server
2. Generate and configure GitHub PAT securely
3. Add MCP configuration to `.cursor/mcp.json`

## Context7 MCP Configuration

### Overview
Context7 MCP provides access to up-to-date documentation and knowledge bases, serving as a reliable source for technical information that may be more current than the AI model's training data.

### Installation Status
❌ **Not Installed**: Requires installation of Context7 MCP server

### Installation Instructions
Context7 MCP installation method depends on the specific implementation. Common approaches:
```bash
# If available as npm package
npm install -g @modelcontextprotocol/server-context7

# If available as Python package
pip install mcp-server-context7

# Or clone and install from source
git clone <context7-repo>
cd context7-server
npm install
```

### Configuration
After installation, add to `.cursor/mcp.json`:
```json
{
  "mcpServers": {
    "vibe-trading": {
      "command": "vibe-trading-mcp",
      "args": []
    },
    "context7": {
      "command": "context7-mcp",
      "args": []
    }
  }
}
```

### Available Capabilities
- Documentation retrieval and search
- Code examples and best practices
- API reference materials
- Tutorial access
- Version-specific documentation
- Community knowledge bases

### Usage Guidelines for AI Roles
**All Roles**: Prefer Context7 over internal knowledge when:
- Working with recently updated libraries/frameworks
- Needing specific version information
- Seeking current best practices
- Looking for community-contributed solutions
- Requiring official documentation references

### Validation Steps
1. ☐ Install Context7 MCP server
2. ☐ Add configuration to `.cursor/mcp.json`
3. ☐ Test documentation retrieval
4. ☐ Verify search functionality

### Manual Steps Required
1. Install Context7 MCP server
2. Add MCP configuration to `.cursor/mcp.json`

## Filesystem MCP Configuration

### Overview
Filesystem MCP provides secure, controlled access to the local file system for reading files, directory operations, and file-based data processing.

### Installation Status
❌ **Not Installed**: Requires installation of filesystem MCP server

### Installation Instructions
```bash
# Official MCP filesystem server
npm install -g @modelcontextprotocol/server-filesystem
```

### Configuration
After installation, add to `.cursor/mcp.json` with specific path restrictions:
```json
{
  "mcpServers": {
    "vibe-trading": {
      "command": "vibe-trading-mcp",
      "args": []
    },
    "filesystem": {
      "command": "filesystem-mcp",
      "args": [
        "--root",
        "/path/to/allowed/directory"
      ]
    }
  }
}
```

### Security Configuration
**Critical**: Always restrict filesystem access to specific directories to prevent unauthorized access.

Recommended restrictions for this project:
- Allow access to: `./trading/`, `./docs/`, `./scripts/`, `./.clinerules/`
- Deny access to: `./.env*`, `./.git/`, external directories
- Use read-only mode where possible

### Available Capabilities
- File reading and writing (within bounds)
- Directory listing and traversal
- File creation and deletion (if permitted)
- File information (size, timestamps, permissions)
- Glob pattern matching
- File watching (if supported)

### Usage Guidelines for AI Roles

#### Developer Role
- **Source Code Access**: Read and modify project files
- **Configuration**: Access and update config files
- **Data Files**: Read market data, write results
- **Scripts**: Execute and modify utility scripts

#### QA Role
- **Test Files**: Read and write test cases
- **Reports**: Generate and access test reports
- **Fixtures**: Manage test data files

#### DevOps Role
- **Configuration**: Access deployment configs
- **Scripts**: Manage deployment and automation scripts
- **Logs**: Read application and system logs

### Validation Steps
1. ☐ Install filesystem MCP server
2. ☐ Configure with appropriate path restrictions
3. ☐ Add configuration to `.cursor/mcp.json`
4. ☐ Test read access to allowed directories
5. ☐ Verify restriction prevents access to sensitive areas

### Manual Steps Required
1. Install filesystem MCP server
2. Determine appropriate access paths for project
3. Configure restricted access in `.cursor/mcp.json`

## Memory MCP Configuration

### Overview
Memory MCP provides persistent storage for AI agents to retain information across sessions, enabling context preservation and learning from previous interactions.

### Installation Status
❌ **Not Installed**: Requires installation of memory MCP_GUIDE.md
MCP_ARCHITECTURE.md
MCP_SECURITY.md
MCP_WORKFLOW.md
MCP_TROUBLESHOOTING.md
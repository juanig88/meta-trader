# MCP Security Guidelines

This document provides security guidelines and best practices for configuring and using Model Context Protocol (MCP) servers in the AI-powered quantitative trading workspace.

## Overview

Security is paramount when integrating external systems with AI agents, especially in financial applications where sensitive data and trading capabilities are involved. This document outlines the security model, potential risks, and mitigation strategies for the MCP ecosystem.

## Security Principles

### 1. Principle of Least Privilege
Each MCP server should operate with the minimum permissions necessary to perform its function. Avoid granting broad access when specific, limited access will suffice.

### 2. Defense in Depth
Implement multiple layers of security rather than relying on a single protective measure.

### 3. Never Trust, Always Verify
Validate all inputs, outputs, and interactions with MCP servers. Assume external connections could be compromised.

### 4. Separation of Concerns
Isolate different security domains (market data, trading execution, development tools) to prevent lateral movement in case of breach.

### 5. Secure by Default
Configurations should be secure without requiring additional hardening. Insecure options should be disabled or require explicit opt-in.

## Authentication and Authorization

### Credential Management
- **Never hardcode credentials** in configuration files or source code
- Use environment variables for secrets: `API_KEY`, `TOKEN`, `PASSWORD`, etc.
- Utilize secret management systems when available (HashiCorp Vault, AWS Secrets Manager, Azure Key Vault)
- Implement credential rotation policies (90-day maximum for most secrets)
- Use short-lived tokens where possible (OAuth 2.0, JWT with short expiration)

### Specific Service Guidelines

#### Vibe Trading MCP
- Typically requires API keys for data providers
- Store keys in environment variables: `VIBE_TRADING_API_KEY`, `ALPHA_VANTAGE_KEY`, etc.
- Verify SSL/TLS connections to data endpoints
- Consider IP whitelisting if supported by data providers

#### CCXT MCP (official `ccxt-mcp`)
- Configure in `.cursor/mcp.json` via `trading/infrastructure/mcp/run_ccxt_mcp.py` — never put keys in `mcp.json`
- Load optional `CCXT_MCP_APIKEY` / `CCXT_MCP_SECRET` from `.env` (gitignored)
- The launcher **strips** `MT5_*`, `TELEGRAM_*`, and other secret-looking keys before spawning Node
- Trading is forced off (`CCXT_MCP_TRADING=0`); this MCP is market data / private reads only
- Exchange API keys should be trade-only if used at all (no withdrawal) and IP-allowlisted

#### Local trading MCP (`local-trading`)
- Read-only snapshot of `bot_state.json` and `trades.log`
- Does **not** receive MT5 passwords and cannot place orders
- Secret-looking JSON keys in bot state are redacted before they reach the model

#### GitHub MCP
- Requires Personal Access Token (PAT) with scoped permissions
- Minimum required scopes: `repo` (for private repos), `read:org`
- Never use personal tokens for service accounts - create dedicated bot accounts
- Consider using GitHub Apps for finer-grained control
- Store token in `GITHUB_TOKEN` environment variable

#### Filesystem MCP
- No authentication required but critical to restrict access
- Use explicit read/write permissions in command arguments
- Consider running in sandboxed or containerized environment for additional isolation

#### QuantConnect MCP
- Authentication handled via extension UI (OAuth flow)
- No manual credential management required in configuration
- Ensure extension is kept up-to-date for security patches

## Network Security

### Outbound-Only Connections
- Configure MCP servers for outbound connections only where possible
- Disable inbound listeners unless explicitly required
- Use firewall rules to block unexpected inbound connections

### Encryption
- Ensure all network communications use TLS 1.2 or higher
- Verify certificate validity for HTTPS endpoints
- Consider certificate pinning for high-security connections
- Use wss:// (WebSocket Secure) instead of ws:// for real-time data

### Transport Security
- **STDIO**: Most secure for local processes - no network exposure
- **SSE/HTTP**: Ensure proper TLS configuration, validate certificates
- **WebSocket**: Use secure variants, implement proper origin validation

## Input Validation and Sanitization

### Parameter Validation
- Validate all parameters passed to MCP tools
- Implement type checking, range validation, and format checking
- Reject malformed or unexpected inputs
- Use allowlists rather than blocklists where possible

### Output Handling
- Sanitize outputs before displaying or further processing
- Be aware of injection risks (command injection, script injection)
- Consider encoding outputs for safe consumption
- Log outputs appropriately for audit trails without exposing sensitive data

## Specific Security Risks and Mitigations

### Risk: Credential Leakage
- **Mitigation**: Use environment variables, secret managers, .gitignore for config files
- **Detection**: Regularly scan repositories for accidental credential commits
- **Response**: Immediate rotation of exposed credentials

### Risk: Unauthorized Data Access
- **Mitigation**: Principle of least privilege, network segmentation
- **Detection**: Access logging and anomaly detection
- **Response**: Immediate access revocation, forensic analysis

### Risk: Malicious MCP Server
- **Mitigation**: Only install from trusted sources, verify package integrity
- **Detection**: Monitor for unexpected behavior or resource usage
- **Response**: Immediate removal, audit of accessed data

### Risk: Data Exfiltration via AI Agent
- **Mitigation**: Monitor and limit outbound communications from AI agent
- **Detection**: Data loss prevention (DLP) tools, unusual transfer patterns
- **Response**: Network segmentation, access review

### Risk: Injection Attacks
- **Mitigation**: Input validation, parameterized queries, output encoding
- **Detection**: Web application firewalls (WAF), input monitoring
- **Response**: Input sanitization, patching vulnerable components

## Configuration Security

### .cursor/mcp.json Protection
- This file should NOT contain secrets
- All authentication should be via environment variables or secure stores
- Ensure proper file permissions (readable only by necessary users/services)
- Consider encrypting the file if it must contain sensitive data (not recommended)

### Environment Variable Security
- Use .env files that are gitignored
- Set appropriate file permissions (600 for .env files)
- Consider using encrypted environment variable stores
- Never echo sensitive environment variables to logs or output

## Audit and Monitoring

### Logging Requirements
- Log all MCP server connections and disconnections
- Log tool invocations with parameters (excluding sensitive data)
- Log authentication events and failures
- Maintain audit trails for sensitive operations (trading, deployment)

### Monitoring
- Set up alerts for unusual MCP server behavior
- Monitor resource usage (CPU, memory, network) for each server
- Track authentication success/failure rates
- Monitor for data exfiltration patterns

### Regular Audits
- Quarterly review of MCP server configurations
- Annual penetration testing of MCP integrations
- Regular review of access logs and audit trails
- Compliance checks against internal and external standards

## Incident Response

### Suspected Compromise
1. Immediately disconnect the suspected MCP server
2. Rotate all credentials associated with that service
3. Review audit logs for unauthorized access
4. Notify relevant stakeholders per incident response plan
5. Perform forensic analysis if necessary
6. Reconnect only after verifying security

### Vulnerability Response
1. Monitor security advisories for MCP server packages
2. Apply security patches within recommended timeframes
3. Test updates in staging before production deployment
4. Maintain inventory of all MCP servers and versions

## Compliance Considerations

### Financial Regulations
- Ensure MCP usage complies with relevant financial regulations (MiFID II, SEC rules, etc.)
- Maintain audit trails for all research and trading activities
- Ensure data retention and privacy requirements are met
- Consider implications of using third-party data sources

### Data Protection
- Comply with GDPR, CCPA, and other data protection regulations
- Implement data minimization principles
- Ensure proper handling of personal data if applicable
- Consider data residency requirements for international operations

## Secure Development Practices

### Third-Party Dependencies
- Regularly update MCP server packages
- Use dependency scanning tools to identify vulnerabilities
- Maintain approved lists of MCP servers and versions
- Consider using software bill of materials (SBOM) for tracking

### Configuration Management
- Use infrastructure as code (IaC) for MCP server deployments where applicable
- Implement configuration drift detection
- Use version control for MCP configurations (excluding secrets)
- Implement change approval processes for production modifications

### Testing
- Include security tests in CI/CD pipelines
- Perform penetration testing on MCP integrations
- Conduct regular security reviews of MCP usage
- Train developers on secure MCP integration practices

## Emergency Procedures

### Immediate Response to Security Incident
1. Isolate affected MCP server(s)
2. Preserve logs and evidence
3. Notify security team and stakeholders
4. Begin investigation while maintaining service continuity where possible
5. Document all actions taken for post-incident review

### Recovery Procedures
1. Verify integrity of backups before restoration
2. Rebuild systems from known good state if necessary
3. Implement additional monitoring during recovery period
4. Conduct post-incident review and update security measures
5. Communicate outcomes to relevant parties

## Appendix: Security Checklist

### Initial Setup
- [ ] All credentials stored in environment variables or secret managers
- [ ] No hardcoded credentials in .cursor/mcp.json or other config files
- [ ] Filesystem MCP configured with restricted access paths
- [ ] GitHub MCP using fine-grained PAT or GitHub App
- [ ] All network communications using TLS 1.2+
- [ ] Unused network ports and services disabled
- [ ] Regular credential rotation schedule established

### Ongoing Operations
- [ ] Audit logging enabled for all MCP interactions
- [ ] Monitoring alerts configured for anomalous behavior
- [ ] Regular security updates applied to MCP servers
- [ ] Quarterly access review performed
- [ ] Incident response plan tested and updated
- [ ] Security training completed for all team members

### Before Adding New MCP Servers
- [ ] Security review completed for new server
- [ ] Principle of least privilege applied to permissions
- [ ] Authentication method evaluated and secured
- [ ] Network requirements assessed and secured
- [ ] Integration tested in isolated environment first
- [ ] Documentation updated with security considerations
# Context7 MCP Configuration Guide

This document provides detailed information about installing, configuring, and using the Context7 MCP server in the Meta-Trader trading bot workspace.

## Overview

Context7 MCP provides access to up-to-date documentation and knowledge bases, serving as a reliable source for technical information that may be more current than the AI model's training data. It helps agents avoid hallucinations and ensures they work with the latest API information, best practices, and examples.

## Installation

### Prerequisites
- Node.js 16+ or Python 3.7+
- npm or pip package manager

### Installation Methods

#### Via npm (Recommended)
```bash
npm install -g @upstash/context7-mcp
```

#### Via pip
```bash
pip install mcp-server-context7
```

#### From Source
```bash
git clone <context7-repository-url>
cd context7-server
npm install
```

## Configuration

After installation, add the Context7 MCP server to your `.cursor/mcp.json` file:

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

### Transport Options
Context7 MCP supports two transport mechanisms:
- **stdio** (default): Standard input/output for local communication
- **http**: HTTP-based communication for remote access

To use HTTP transport:
```json
{
  "mcpServers": {
    "context7": {
      "command": "context7-mcp",
      "args": ["--transport", "http", "--port", "3000"]
    }
  }
}
```

### Authentication
Context7 MCP supports optional API key authentication:
```json
{
  "mcpServers": {
    "context7": {
      "command": "context7-mcp",
      "args": ["--api-key", "your-api-key-here"]
    }
  }
}
```

Alternatively, set the `CONTEXT7_API_KEY` environment variable.

## Usage Guidelines

### When to Use Context7
AI agents should consult Context7 before relying on internal knowledge when:
- Working with recently updated libraries/frameworks
- Needing specific version information
- Seeking current best practices
- Looking for community-contributed solutions
- Requiring official documentation references
- Verifying API signatures, parameters, or return values
- Checking for deprecation notices

### Role-Specific Usage

#### Risk Quant Role
- Verify quantitative library APIs (NumPy, Pandas, SciPy, etc.)
- Check latest versions of statistical/mathematical packages
- Confirm financial modeling library usage
- Validate data science workflow documentation

#### Technical Lead Role
- Check architectural patterns and best practices
- Verify framework-specific implementation guidelines
- Confirm coding standards and conventions
- Review performance optimization techniques

#### Developer Role
- Look up specific library/API usage
- Verify function signatures and parameters
- Check version compatibility
- Find code examples and tutorials
- Validate implementation approaches

#### QA Engineer Role
- Verify testing framework APIs
- Check mocking library usage
- Confirm assertion library syntax
- Review test organization best practices

#### DevOps Engineer Role
- Check infrastructure tool documentation
- Verify CI/CD platform integrations
- Confirm container/orchestration guidelines
- Review monitoring and logging solutions

#### Product Owner Role
- Research market trends and competitive analysis
- Check industry standards and regulations
- Verify feature implementation guidelines
- Review user experience best practices

## Validation

### Basic Connection Test
```bash
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test-client","version":"1.0.0"}}}' | context7-mcp
```

### Tools List Test
```bash
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test-client","version":"1.0.0"}}}' > init.json && 
echo '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' > tools.json && 
type init.json tools.json | context7-mcp
```

### Documentation Query Test
```bash
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test-client","version":"1.0.0"}}}' > init.json && 
echo '{"jsonrpc":"2.0","id":2,"method":"resources/list","params":{}}' > resources.json && 
type init.json resources.json | context7-mcp
```

## Troubleshooting

### Common Issues

#### Server Fails to Start
- Verify Node.js/Python installation
- Check package installation: `npm list -g @upstash/context7-mcp` or `pip show mcp-server-context7`
- Ensure executable is in PATH

#### Connection Problems
- Verify stdio vs http configuration
- Check firewall settings (for HTTP mode)
- Confirm port availability (for HTTP mode)

#### Authentication Errors
- Verify API key correctness
- Check environment variable setting
- Ensure token hasn't expired or been revoked

#### No Documentation Found
- Verify search terms are correct
- Try alternative naming conventions
- Check if documentation source is accessible
- Consider contributing missing documentation

## Maintenance

### Updates
Regularly update the Context7 MCP server:
```bash
npm update -g @upstash/context7-mcp
# or
pip upgrade mcp-server-context7
```

### Monitoring
- Monitor logs for errors or warnings
- Check response times for documentation queries
- Verify successful connection establishment

## Security Considerations

### Data Privacy
- Context7 only accesses public documentation sources
- No sensitive data is transmitted to Context7 servers
- Review any enterprise-specific documentation sources

### Network Security
- Uses standard HTTP/HTTPS for document retrieval
- Ensure outbound HTTPS access is allowed
- Consider proxy configuration if required

## Integration with Other MCP Servers

Context7 MCP works alongside other MCP servers in the ecosystem:
- Use with Vibe Trading MCP for quantitative research validation
- Use with GitHub MCP for code examples and repository documentation
- Use with Filesystem MCP to save retrieved documentation
- Use with Memory MCP to cache frequently accessed documentation

## Resources

- Context7 Official Documentation: https://context7.com
- npm Package: https://www.npmjs.com/package/@upstash/context7-mcp
- GitHub Repository: https://github.com/upstash/context7-mcp
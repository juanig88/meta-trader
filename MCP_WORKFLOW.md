# MCP Workflow Documentation

This document outlines the standard workflows for using Model Context Protocol (MCP) servers in the AI-powered quantitative trading workspace. It defines how different AI roles should interact with MCP servers to accomplish their tasks effectively and securely.

## Overview

MCP workflows provide standardized procedures for leveraging external tools, data sources, and services through the Model Context Protocol. These workflows ensure consistency, security, and efficiency across all AI agents in the system.

Live MT5 orders in this repo go through `swing_daily.py` (daily Donchian swing, demo XBTFX, since 2026-10-07), not through MCP write tools. The rotation (`bot_mt5.py`) is stopped. `local-trading` is snapshot-only. See `trading/docs/SWING_ENGINE.md`.

## Core Workflow Principles

### 1. Discovery First
Before attempting any operation, first discover what capabilities are available through the appropriate MCP servers.

### 2. Validate Before Action
Always verify data, configurations, and assumptions using MCP tools before proceeding with actions.

### 3. Least Privilege Access
Use only the minimum necessary permissions and access required for each task.

### 4. Audit Trail
Ensure all significant actions leave an audit trail through logging or documentation.

### 5. Error Handling
Implement proper error handling and fallback mechanisms for MCP interactions.

## Standard Workflow Templates

### Workflow 1: Research and Data Gathering
Used by: Research Analyst, Risk Quant, Technical Lead

1. **Define Research Question**
   - Clearly articulate what information is needed
   - Determine timeframe, instruments, and metrics required

2. **Select Appropriate MCP Server**
   - Market data: Vibe Trading MCP
   - Fundamental data: Vibe Trading MCP or specialized sources
   - Alternative data: Vibe Trading MCP or other relevant sources
   - Documentation: Context7 MCP

3. **Discover Available Tools**
   - Use `list_resources` and `list_tools` to see what's available
   - Check for specific data endpoints or analysis functions

4. **Execute Data Retrieval**
   - Call appropriate data fetching tools
   - Apply filters and parameters as needed
   - Handle pagination for large datasets

5. **Validate Results**
   - Check for data completeness and quality
   - Verify timestamps and data integrity
   - Cross-reference with alternative sources if critical

6. **Process and Analyze**
   - Perform initial calculations or transformations
   - Identify patterns or anomalies
   - Prepare data for deeper analysis

7. **Document Findings**
   - Record sources, timestamps, and methodologies
   - Note any limitations or data quality issues
   - Store results in appropriate location (Filesystem MCP or Memory MCP)

### Workflow 2: Strategy Development and Backtesting
Used by: Strategy Developer, Risk Quant, Quant Researcher

1. **Strategy Concept Definition**
   - Define hypothesis and trading logic
   - Specify entry/exit conditions, position sizing
   - Identify required data inputs

2. **Data Acquisition (Workflow 1)**
   - Gather historical price data
   - Collect fundamental or alternative data if needed
   - Ensure data covers sufficient time periods and market regimes

3. **Environment Setup**
   - Check available backtesting engines (Vibe Trading MCP, QuantConnect MCP)
   - Verify required libraries and dependencies
   - Set up initial capital, commission, and slippage models

4. **Strategy Implementation**
   - Code strategy logic using available tools
   - Implement risk management rules
   - Add logging and performance tracking

5. **Backtest Execution**
   - Run initial backtest with default parameters
   - Monitor for errors or unexpected behavior
   - Validate trade logic against expectations

6. **Performance Analysis**
   - Calculate key metrics (Sharpe, Sortino, max drawdown, win rate)
   - Analyze equity curve and drawdown periods
   - Examine trade distribution and statistics

7. **Robustness Testing**
   - Perform walk-forward analysis
   - Test on out-of-sample data
   - Conduct parameter sensitivity analysis
   - Check for overfitting signs

8. **Risk Validation**
   - Verify risk controls function correctly
   - Test under stress scenarios
   - Validate position sizing and exposure limits

9. **Documentation and Review**
   - Document methodology, assumptions, and results
   - Prepare for peer review or risk committee presentation
   - Store code and results in version control

### Workflow 3: Trade Execution Preparation
Used by: Execution Specialist, Trading Engineer, Risk Quant (validation)

1. **Pre-Trade Validation**
   - Verify strategy has passed all research and backtesting phases
   - Confirm risk parameters are within approved limits
   - Check operational readiness (connectivity, funding, etc.)

2. **Environment Check**
   - Validate exchange connectivity
   - Confirm account status and permissions
   - Verify market data feeds are operational

3. **Risk Checks**
   - Run pre-trade risk assessment using risk management tools
   - Validate position sizes against limits
   - Check for conflicting positions or exposures

4. **Order Preparation**
   - Format orders according to exchange requirements
   - Set appropriate order types (limit, market, stop, etc.)
   - Attach necessary metadata (strategy ID, timestamp, etc.)

5. **Execution Monitoring**
   - Submit orders through approved channels
   - Monitor fill status and slippage
   - Track order lifecycle from submission to settlement

6. **Post-Trade Analysis**
   - Compare expected vs. actual execution
   - Analyze market impact and timing
   - Identify any execution anomalies

7. **Reporting and Logging**
   - Record all trade details for audit trail
   - Update performance tracking systems
   - Notify relevant stakeholders of execution

### Workflow 4: Code Development and Review
Used by: Developer, QA Engineer, Technical Lead

1. **Requirement Analysis**
   - Review user stories or technical specifications
   - Identify required functionality and constraints
   - Determine dependencies and integration points

2. **Research and Planning**
   - Consult documentation via Context7 MCP
   - Review existing codebase via Filesystem and GitHub MCP
   - Identify similar implementations or patterns

3. **Development Environment Setup**
   - Ensure necessary tools and libraries are available
   - Configure development environment consistently
   - Set up version control branches if needed

4. **Implementation**
   - Write modular, testable code
   - Follow established coding standards and patterns
   - Implement appropriate error handling and logging

5. **Local Testing**
   - Run unit tests to verify functionality
   - Perform manual testing for edge cases
   - Use debugging tools as needed

6. **Code Review Preparation**
   - Ensure code follows style guidelines
   - Write clear commit messages
   - Prepare summary of changes for reviewers

7. **Peer Review Process**
   - Submit pull request via GitHub MCP
   - Address reviewer comments and suggestions
   - Ensure all checks pass (CI/CD, security scans)

8. **Integration and Testing**
   - Merge approved changes to main branch
   - Run integration tests to verify compatibility
   - Perform staging environment validation if applicable

9. **Deployment Preparation**
   - Create deployment package or configuration
   - Prepare rollback plan
   - Schedule deployment during appropriate window

10. **Post-Deployment Monitoring**
    - Monitor system behavior after deployment
    - Check for errors or performance issues
    - Be ready to rollback if necessary

### Workflow 5: Performance Monitoring and Optimization
Used by: Performance Engineer, DevOps Engineer, Technical Lead

1. **Metric Collection**
   - Gather system performance data (latency, throughput, resource usage)
   - Collect application-specific metrics (signal generation speed, execution quality)
   - Monitor business metrics (P&L, win rate, drawdown)

2. **Baseline Establishment**
   - Define normal operating ranges for all metrics
   - Establish service level objectives (SLOs)
   - Create dashboards for visualization

3. **Anomaly Detection**
   - Set up alerts for metric deviations
   - Identify trends or patterns indicating degradation
   - Correlate system metrics with business outcomes

4. **Root Cause Analysis**
   - Use profiling tools to identify bottlenecks
   - Analyze logs for error patterns or warnings
   - Correlate events across different system components

5. **Optimization Planning**
   - Prioritize issues based on impact and frequency
   - Develop hypotheses for performance improvements
   - Estimate effort and risk for each optimization

6. **Implementation and Testing**
   - Implement optimizations in controlled manner
   - Performance test changes before production deployment
   - Verify functional correctness is maintained

7. **Validation and Measurement**
   - Measure actual performance improvements
   - Compare against baseline and expectations
   - Document results and lessons learned

8. **Continuous Improvement**
   - Feed insights back into development process
   - Update monitoring and alerting as needed
   - Schedule regular performance reviews

## Role-Specific MCP Usage Guidelines

### Risk Quant Role
**Primary MCP Servers:**
- Vibe Trading MCP (market data, backtesting, validation)
- QuantConnect MCP (secondary validation)
- Sequential Thinking MCP (complex risk analysis)
- Context7 MCP (quantitative methodologies, regulatory guidance)

**Typical Workflows:**
1. Market data retrieval and validation
2. Strategy backtesting with realistic cost modeling
3. Factor exposure analysis
4. Risk metric calculation (VaR, CVaR, drawdown)
5. Stress testing and scenario analysis
6. Model validation and backtesting oversight
7. Approval/rejection decisions with documented rationale

### Technical Lead Role
**Primary MCP Servers:**
- GitHub MCP (code review, repository management)
- Context7 MCP (architecture patterns, best practices)
- Filesystem MCP (code navigation, configuration)
- Optional: Docker MCP (containerization questions)

**Typical Workflows:**
1. Architecture review and design validation
2. Code quality and standards enforcement
3. Dependency and library evaluation
4. Performance optimization guidance
5. Security vulnerability assessment
6. Technical debt identification and planning
7. Mentorship and knowledge sharing facilitation

### Developer Role
**Primary MCP Servers:**
- Filesystem MCP (source code access, modification)
- GitHub MCP (version control, collaboration)
- Context7 MCP (documentation, examples, best practices)
- Vibe Trading MCP (research for implementation)

**Typical Workflows:**
1. Feature implementation according to specifications
2. Bug fixing and issue resolution
3. Code refactoring and technical debt reduction
4. Unit test creation and execution
5. Integration with existing systems
6. Performance optimization of critical paths
7. Documentation updates and code comments

### QA Engineer Role
**Primary MCP Servers:**
- GitHub MCP (test file access, issue tracking)
- Filesystem MCP (test execution, report generation)
- Optional: Playwright MCP (end-to-end testing)
- Vibe Trading MCP (validation of quantitative components)

**Typical Workflows:**
1. Test plan development and case creation
2. Automated test script creation and maintenance
3. Manual testing for edge cases and usability
4. Performance and load testing coordination
5. Security testing and vulnerability assessment
6. Release candidate validation and sign-off
7. Defect tracking and regression testing

### DevOps Engineer Role
**Primary MCP Servers:**
- Filesystem MCP (configuration, scripts)
- Optional: Docker MCP (container management, orchestration)
- Optional: Kubernetes MCP (cluster operations)
- Optional: Monitoring/Metrics MCP (observability tools)
- GitHub MCP (deployment scripts, infrastructure as code)

**Typical Workflows:**
1. Environment provisioning and configuration
2. CI/CD pipeline setup and maintenance
3. Deployment automation and release management
4. Monitoring, logging, and alerting configuration
5. Incident response and system recovery
6. Capacity planning and scalability improvements
7. Security patching and system hardening

### Product Owner Role
**Primary MCP Servers:**
- Context7 MCP (market research, competitive analysis)
- GitHub MCP (feature tracking, progress monitoring)
- Filesystem MCP (documentation, requirements)
- Optional: Analytics MCP (usage metrics, feedback analysis)

**Typical Workflows:**
1. Requirements gathering and prioritization
2. User story creation and refinement
3. Progress tracking and reporting
4. Stakeholder communication and expectation management
5. Release planning and coordination
6. Feedback collection and analysis
7. Market and competitive landscape monitoring

## Error Handling and Fallback Procedures

### Common MCP Issues and Solutions

#### Connection Failures
1. Verify MCP server is running and accessible
2. Check network connectivity and firewall settings
3. Confirm correct command and arguments in configuration
4. Restart MCP server if necessary
5. Fallback to cached data or manual methods if available

#### Authentication Errors
1. Verify credentials are correctly configured
2. Check expiration dates on tokens or keys
3. Ensure proper scopes or permissions are granted
4. Renew or regenerate credentials as needed
5. Use alternative authentication methods if available

#### Tool Execution Errors
1. Validate input parameters against schema and parameters against tool specification
2. Check for required dependencies or prerequisites
3. Review error messages for specific guidance
4. Try simplified version of the request
5. Consult documentation via Context7 MCP for correct usage

#### Data Quality Issues
1. Verify data source status and availability
2. Check for known issues with specific data providers
3. Validate date ranges and symbol correctness
4. Consider alternative data sources if available
5. Flag data quality concerns in analysis and reporting

#### Performance Problems
1. Monitor resource usage (CPU, memory, network)
2. Check for blocking operations or deadlocks
3. Consider batching or pagination for large requests
4. Evaluate whether operation can be deferred or scheduled
5. Optimize queries or requests to reduce data transfer

### Escalation Procedures
1. Document issue with timestamps, error messages, and steps to reproduce
2. Attempt basic troubleshooting (restart, reconnect, verify config)
3. Consult team knowledge base or documentation
4. Escalate to subject matter expert or technical lead
5. Engage vendor support if third-party service issue
6. Implement workaround or manual process if critical

## Best Practices for MCP Usage

### Efficiency Optimization
- Batch related requests when possible
- Cache frequently accessed data appropriately
- Use streaming or pagination for large datasets
- Pre-warm connections for latency-sensitive operations
- Schedule non-urgent tasks during off-peak hours

### Reliability Enhancement
- Implement retry mechanisms with exponential backoff
- Use circuit breaker patterns for failing services
- Implement fallback to cached or alternative data sources
- Regular health checks for critical MCP servers
- Monitor and alert on error rates and latency

### Security Maintenance
- Regularly rotate credentials and tokens
- Keep MCP server software up to date
- Review and audit access logs periodically
- Test permission boundaries and restrictions
- Stay informed about security advisories for used services

### Collaboration and Knowledge Sharing
- Document useful MCP tool combinations and patterns
- Share effective workflows and techniques with team
- Create examples and templates for common tasks
- Conduct regular training sessions on MCP usage
- Maintain a registry of available MCP servers and capabilities

## Appendix: Common MCP Tool Patterns

### Data Retrieval Pattern
```
1. List available resources/data sources
2. Filter or search for specific dataset
3. Retrieve metadata about dataset (schema, size, update frequency)
4. Fetch data with appropriate parameters (date range, symbols, etc.)
5. Validate received data (completeness, format, timestamps)
6. Process or transform as needed for analysis
```

### Analysis Execution Pattern
```
1. Verify required data is available and loaded
2. Check for required libraries or dependencies
3. Configure analysis parameters and settings
4. Execute analysis function or algorithm
5. Monitor progress and resource usage
6. Collect and validate results
7. Format output for reporting or further processing
```

### Validation and Verification Pattern
```
1. Define validation criteria and acceptable thresholds
2. Execute process or generate output to be validated
3. Apply validation checks (range, format, consistency, etc.)
4. Document any deviations or failures
5. Determine if issues are acceptable or require remediation
6. Update process or fix issues as needed
7. Re-validate after changes if necessary
```

### Reporting and Documentation Pattern
```
1. Gather all required data and results
2. Apply any necessary formatting or transformations
3. Generate visualizations or summaries if appropriate
4. Create narrative explanation of findings
5. Include methodology, assumptions, and limitations
6. Review for accuracy and completeness
7. Distribute to appropriate stakeholders
8. Archive according to retention policies
```

## Change Management

### Adding New MCP Servers
1. Conduct security and privacy review
2. Test in isolated environment before production use
3. Document installation and configuration procedures
4. Update relevant workflow documentation
5. Train team members on new capabilities
6. Monitor initial usage for issues or unexpected behavior

### Modifying Existing Workflows
1. Assess impact on current operations
2. Update documentation before implementation
3. Communicate changes to affected team members
4. Provide transition period if significant changes
5. Monitor for issues after implementation
6. Collect feedback and iterate as needed

### Deprecating MCP Servers
1. Provide advance notice to users
2. Offer migration assistance or alternatives
3. Maintain access for critical historical needs if possible
4. Remove from configuration after grace period
5. Archive documentation for reference
6. Verify no critical dependencies remain

## Conclusion

Effective use of MCP servers requires disciplined adherence to workflows, security practices, and continuous improvement. By following the guidelines in this document, AI agents can leverage external capabilities safely and efficiently to enhance their performance in quantitative trading development, research, and operations.

Regular review and updates to these workflows will ensure they remain effective as the MCP ecosystem evolves and new capabilities become available.
# Context7 MCP Best Practices

This document outlines best practices for using the Context7 MCP server effectively and responsibly in the Meta-Trader trading bot workspace.

## Core Principles

### 1. Documentation-First Mindset
Always consult official documentation through Context7 before relying on internal knowledge or making assumptions about APIs, libraries, or frameworks.

### 2. Verification Over Assumption
Never assume you know how something works—always verify with current documentation, especially for frequently updated libraries.

### 3. Context Awareness
Consider how documentation applies to your specific use case, version, and environment before implementing solutions.

### 4. Attribution and Traceability
When providing information derived from documentation, reference the source to maintain transparency and enable verification.

## Specific Best Practices

### Query Formulation

#### Be Specific and Precise
- ❌ "How do I use React hooks?"
- ✅ "What are the rules for using useEffect with async cleanup functions in React 18?"

#### Include Relevant Context
Always specify:
- Programming language and version
- Library/framework name and version
- Specific use case or problem
- Environment constraints (if applicable)

#### Use Correct Terminology
- Use official terms from the documentation
- Avoid colloquialisms or slang that might confuse search
- Include error messages verbatim when troubleshooting

### Result Evaluation

#### Check Source Credibility
- Prioritize official documentation over community sources
- Verify the documentation source is authoritative
- Check for institutional or project backing

#### Validate Timeliness
- Look for publication or last-modified dates
- Ensure version information matches your needs
- Be wary of outdated information, especially for fast-moving projects

#### Assess Completeness
- Check if examples are runnable and complete
- Look for edge case coverage and error handling
- Verify parameter descriptions include types and constraints

### Implementation Approach

#### Test Before Trusting
- Implement solutions in a sandbox or test environment first
- Verify behavior matches documentation expectations
- Test boundary conditions and error cases

#### Document Your Understanding
- Keep notes on what you learned from documentation
- Record any assumptions or interpretations
- Note discrepancies between documentation and observed behavior

#### Version Considerations
- Always check if multiple versions are documented
- Verify which version applies to your project
- Note any breaking changes or deprecations

## Role-Specific Best Practices

### Risk Quant Specific Practices

#### Mathematical Validation
- Verify numerical precision and accuracy claims
- Check for stability warnings in statistical functions
- Validate random number generation algorithms
- Confirm distribution parameters match conventions

#### Financial Model Verification
- Cross-reference formulas with authoritative sources
- Check day count convention implementations
- Validate currency conversion precision
- Verify risk calculation methodologies

### Technical Lead Specific Practices

#### Architecture Validation
- Check scalability claims against documented benchmarks
- Verify fault tolerance mechanisms
- Review consistency guarantees in distributed systems
- Confirm security implementation details

#### Framework Guidance Compliance
- Ensure project structure follows official recommendations
- Validate configuration approaches
- Check extension point usage against documented patterns
- Verify performance optimization techniques

### Developer Specific Practices

#### API Integration
- Always verify authentication requirements
- Check rate limiting and quota information
- Review error code meanings and handling recommendations
- Confirm data format specifications (JSON/XML/etc.)

#### Library Usage
- Verify thread-safety guarantees
- Check resource cleanup requirements
- Validate initialization and shutdown sequences
- Confirm callback execution contexts

### QA Specific Practices

#### Test Strategy Validation
- Verify coverage measurement methodologies
- Check test environment isolation recommendations
- Validate test data management approaches
- Confirm continuous integration integration points

#### Test Tool Evaluation
- Check compatibility with your technology stack
- Verify licensing for your intended use
- Validate performance and scalability claims
- Confirm reporting and integration capabilities

### DevOps Specific Practices

#### Infrastructure Validation
- Review security best practices for cloud providers
- Check compliance certification documentation
- Verify backup and disaster recovery procedures
- Confirm monitoring and alerting capabilities

#### Pipeline Optimization
- Verify caching strategies and limitations
- Check artifact storage and retention policies
- Validate security scanning integration points
- Confirm approval workflow customization options

### Product Owner Specific Practices

#### Market Analysis
- Verify data sources and collection methodologies
- Check sample sizes and statistical significance
- Confirm temporal relevance of data
- Validate analytical approaches and assumptions

#### Feature Specification
- Check accessibility standard implementations
- Verify internationalization and localization approaches
- Confirm regulatory compliance requirements
- Validate user experience guidelines adherence

## Safety and Ethics

### Security Consciousness
- Never include API keys, passwords, or tokens in queries
- Be aware of what information might be logged or stored
- Consider implications for proprietary or confidential technologies
- Report security vulnerabilities through proper channels

### Intellectual Property Respect
- Check licensing before implementing documented approaches
- Provide attribution when using code examples from documentation
- Respect usage restrictions on proprietary documentation
- Avoid reproducing large portions of copyrighted documentation

### Bias Awareness
- Recognize that documentation may have organizational biases
- Seek multiple sources for controversial or complex topics
- Be aware of documentation gaps or omissions
- Consider alternative implementations or approaches

## Maintenance and Currency

### Regular Verification
- Schedule periodic re-verification of critical dependencies
- Check for documentation updates when changing versions
- Validate assumptions when environment changes occur
- Re-check before major releases or deployments

### Version Tracking
- Maintain a registry of versions for mission-critical components
- Subscribe to change notifications for key dependencies
- Document compatibility matrices between systems
- Plan upgrade paths based on deprecation timelines

### Feedback Loops
- Contribute to documentation when you identify gaps or errors
- Share useful patterns or implementations with maintainers
- Participate in community documentation improvement efforts
- Report outdated or dangerous recommendations

## Performance Optimization

### Efficient Query Patterns
- Batch related questions to minimize context switching
- Cache frequently accessed documentation snippets
- Use specific, targeted queries rather than exploratory searches
- Leverage previous context to refine follow-up questions

### Resource Management
- Monitor response times for different query types
- Implement timeouts for unresponsive documentation queries
- Use asynchronous patterns when possible to avoid blocking
- Consider predictive prefetching for anticipated needs

## Troubleshooting Guidance

### When Documentation Seems Wrong
1. Verify you're looking at the correct version
2. Check for known issues or errata listings
3. Look for community discussions or bug reports
4. Consider environmental factors or configuration differences
5. Test empirically to confirm behavior

### When Documentation Is Incomplete
1. Check related sections or modules for complementary information
2. Look for examples that might illustrate the missing concept
3. Consider checking source code comments if available
4. Consult community forums or Stack Overflow as secondary sources
5. Document assumptions and create test cases to validate

### When Documentation Is Overwhelming
1. Use search functions within documentation when available
2. Look for quick start guides or tutorial sections
3. Check for cheat sheets or reference cards
4. Consider video tutorials or walkthroughs for complex topics
5. Break down complex topics into manageable chunks

## Integration with Workflow Tools

### IDE Integration
- Consider plugins that bring documentation lookup into your editor
- Configure keyboard shortcuts for quick documentation access
- Set up contextual help based on current code context
- Integrate with error highlighting for immediate lookup

### Documentation Generation
- Use insights from external documentation to improve internal docs
- Create summary documents for frequently referenced external sources
- Maintain a knowledge base of verified information from documentation
- Generate quick-reference guides based on commonly accessed documentation

### Knowledge Sharing
- Document useful findings from documentation sessions
- Create team wiki entries for commonly referenced information
- Share effective query formulations with team members
- Maintain a repository of validated code snippets from documentation

## Quality Assurance

### Peer Review
- Have team members verify critical documentation-based decisions
- Cross-check implementations against multiple sources when possible
- Validate understanding through pair programming or discussion
- Create checklists for documentation verification steps

### Testing Strategies
- Create test cases that validate documented behavior
- Use property-based testing to explore edge cases
- Implement monitoring for deviations from documented expectations
- Create regression tests when documentation changes affect implementation

### Audit Trails
- Log documentation queries for critical decisions
- Maintain version references for all external information used
- Document rationale for choosing specific documentation sources
- Track changes when updating based on new documentation

## Special Considerations for Financial Systems

### Regulatory Compliance
- Verify compliance implementations against official regulations
- Check for regional variations in financial regulations
- Validate audit trail and reporting requirements
- Confirm record retention specifications

### Risk Calculation Validation
- Cross-reference risk formulas with authoritative sources (Basel, ISDA, etc.)
- Validate model assumptions against regulatory guidelines
- Check backtesting requirements and limitations
- Confirm stress testing methodologies

### Data Integrity
- Verify data validation and sanitization procedures
- Check numerical precision requirements for financial calculations
- Confirm timestamp handling and timezone conventions
- Validate data retention and archival policies

## Conclusion

Effective use of Context7 MCP is not merely about accessing documentation—it's about developing a disciplined approach to knowledge acquisition and verification. By consistently following these best practices, AI agents can significantly reduce errors, improve implementation quality, and ensure they're building on reliable, verified information.

The key principles to remember are:
1. Always verify with current documentation
2. Be specific and contextual in your queries
3. Evaluate sources critically
4. Test before trusting
5. Document your sources and reasoning
6. Stay current through regular verification
7. Contribute back when possible

By making these practices habitual, the team can collectively maintain a high standard of technical excellence while adapting to the ever-evolving landscape of software development and financial technology.
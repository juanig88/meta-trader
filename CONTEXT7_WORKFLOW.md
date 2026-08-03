# Context7 MCP Workflow Documentation

This document outlines specific workflows for using the Context7 MCP server in the Meta-Trader trading bot workspace, detailing how different AI roles should leverage documentation lookup for their tasks.

## Core Principles for Context7 Usage

1. **Documentation First**: Always check official documentation before relying on internal knowledge
2. **Version Specificity**: Verify you're looking at the correct version of documentation
3. **Cross-Reference**: Validate information across multiple sources when critical
4. **Context Awareness**: Consider how documentation applies to your specific use case
5. **Attribution**: Reference the source when providing information from documentation

## Standard Workflows Using Context7

### Workflow 1: Library/API Research
Used by: All roles when needing to understand or use a specific library/API

1. **Identify the Need**
   - Determine what library, framework, or tool you need information about
   - Specify the exact functionality or API you're researching
   - Note the version you're working with (if known)

2. **Formulate Your Query**
   - Create a clear, specific question about the documentation
   - Include relevant context (language, version, use case)
   - Example: "How do I configure retry policies in Polly v7 for .NET 6?"

3. **Execute Documentation Search**
   - Use Context7 MCP to search for relevant documentation
   - Try different search terms if initial results aren't satisfactory
   - Filter by version if multiple versions are available

4. **Analyze Results**
   - Read the retrieved documentation carefully
   - Look for code examples, parameter descriptions, and return values
   - Note any version-specific considerations or deprecation warnings

5. **Apply Findings**
   - Implement the solution based on documentation
   - Verify your implementation matches the documented approach
   - Test edge cases mentioned in the documentation

### Workflow 2: Version Migration Planning
Used by: Technical Lead, Developer when upgrading libraries/frameworks

1. **Identify Current and Target Versions**
   - Document what version you're currently using
   - Determine what version you want to upgrade to
   - Check release notes for breaking changes

2. **Research Breaking Changes**
   - Use Context7 to find migration guides
   - Look for "breaking changes" or "migration" sections
   - Identify API removals, deprecations, or behavior changes

3. **Create Migration Plan**
   - List all required code changes
   - Identify deprecated features to replace
   - Note any configuration changes needed
   - Estimate effort for each change

4. **Implement and Test**
   - Make changes incrementally where possible
   - Use documentation to guide each change
   - Test thoroughly after each major change

### Workflow 3: Best Practices Verification
Used by: All roles when implementing new features or reviewing code

1. **Define the Implementation Area**
   - Specify what functionality you're implementing
   - Identify relevant libraries/frameworks involved
   - Note any performance, security, or maintainability concerns

2. **Search for Best Practices**
   - Query Context7 for best practices related to your implementation
   - Look for official guidelines, recommended patterns, and anti-patterns
   - Check for performance optimization guides

3. **Evaluate Against Current Approach**
   - Compare your planned/current approach with documented best practices
   - Identify gaps or deviations
   - Determine if deviations are justified or need correction

4. **Refine Implementation**
   - Adjust your approach to align with best practices
   - Document any exceptions with justification
   - Ensure team awareness of the chosen approach

### Workflow 4: Troubleshooting and Debugging
Used by: Developer, QA Engineer when encountering issues

1. **Define the Problem Clearly**
   - Describe the exact error or unexpected behavior
   - Include stack traces, error messages, and reproduction steps
   - Note the environment (OS, versions, dependencies)

2. **Search for Similar Issues**
   - Use Context7 to look for troubleshooting guides
   - Search for error messages or symptom descriptions
   - Check FAQ sections and common problem documentation

3. **Apply Diagnostic Techniques**
   - Follow recommended debugging approaches from documentation
   - Use suggested logging or diagnostic tools
   - Verify configuration against documented requirements

4. **Implement Solution**
   - Apply fixes or workarounds from documentation
   - Test thoroughly to ensure resolution
   - Document the solution for future reference

## Role-Specific Context7 Workflows

### Risk Quant Role Workflows

#### Quantitative Library Validation
1. Identify the statistical/mathematical operation needed
2. Search for relevant functions in libraries like NumPy, SciPy, or pandas
3. Verify function signatures, parameters, and return values
4. Check for numerical stability notes or precision considerations
5. Validate implementation against documented examples

#### Financial Modeling Verification
1. Determine the financial model or calculation being implemented
2. Search for domain-specific libraries (QuantLib, etc.)
3. Verify formula implementations against documented references
4. Check for assumptions and limitations documented
5. Validate parameter ranges and constraints

### Technical Lead Role Workflows

#### Architecture Pattern Validation
1. Identify the architectural pattern being considered
2. Search for official documentation on that pattern
3. Review benefits, drawbacks, and use cases
4. Check for framework-specific implementations
5. Validate against project requirements and constraints

#### Framework Guidelines Review
1. Identify the framework being used (e.g., .NET, Spring, Django)
2. Search for official architecture and design guidelines
3. Review recommended project structure and organization
4. Check for performance and scalability guidelines
5. Ensure team practices align with documented recommendations

### Developer Role Workflows

#### API Implementation
1. Identify the external API or service to integrate with
2. Search for official SDK or API documentation
3. Review authentication methods and requirements
4. Check rate limiting, pagination, and error handling guidelines
5. Implement according to documented best practices

#### Library Usage Verification
1. Identify the library function or feature to use
2. Search for official documentation of that specific functionality
3. Verify parameter types, order, and defaults
4. Check return value types and possible exceptions
5. Look for performance considerations or usage notes

### QA Engineer Role Workflows

#### Test Framework Validation
1. Identify the testing framework being used (e.g., Jest, PyTest, NUnit)
2. Search for best practices in test organization and naming
3. Review assertion library documentation and recommendations
4. Check for mocking/stubbing guidelines and limitations
5. Ensure test cases follow documented approaches

#### Test Automation Strategies
1. Identify the type of testing (unit, integration, E2E)
2. Search for recommended strategies and tools
3. Review documentation on test data management
4. Check for CI/CD integration guidelines
5. Validate approach against documented best practices

### DevOps Engineer Role Workflows

#### Infrastructure as Code Review
1. Identify the IaC tool being used (Terraform, CloudFormation, etc.)
2. Search for best practices in module organization and reuse
3. Review state management and locking recommendations
4. Check for security and secret handling guidelines
5. Validate implementation against documented approaches

#### CI/CD Pipeline Optimization
1. Identify the CI/CD platform (GitHub Actions, GitLab CI, etc.)
2. Search for performance optimization techniques
3. Review caching strategies and artifact management
4. Check for security scanning and approval workflow guidelines
5. Ensure pipeline design follows documented best practices

### Product Owner Role Workflows

#### Market Research Validation
1. Identify the market trend or technology being researched
2. Search for industry reports, analyst perspectives, and expert opinions
3. Verify information against multiple reputable sources
4. Check for recent developments and future projections
5. Ensure recommendations are based on current, documented information

#### Feature Specification Review
1. Identify the feature or functionality being specified
2. Search for existing implementations or references in similar domains
3. Review user experience guidelines and accessibility standards
4. Check for regulatory or compliance requirements documentation
5. Validate specifications against documented best practices

## Context7 Integration with Development Workflow

### Pre-Implementation Phase
- Research technologies and libraries before selection
- Verify compatibility and requirements through documentation
- Check licensing and usage restrictions
- Evaluate community support and documentation quality

### Implementation Phase
- Consult documentation before writing code
- Verify API usage and parameter types
- Check for initialization and cleanup requirements
- Review error handling and edge case documentation

### Review and Testing Phase
- Verify implementation against documented best practices
- Check for missed error handling or edge cases
- Ensure configuration follows recommended approaches
- Validate performance considerations are addressed

### Maintenance Phase
- Schedule regular documentation reviews for critical dependencies
- Check for deprecation warnings and planned removals
- Verify security updates and vulnerability disclosures
- Update implementation to align with evolving best practices

## Error Handling and Fallback Procedures

### When Documentation Is Not Found
1. Verify search terms and try alternatives
2. Check if the library/tool might be known by different names
3. Consider if the feature might be version-specific
4. Fall back to official websites or repositories
5. Consider community sources (Stack Overflow, GitHub issues) with caution

### When Documentation Appears Contradictory
1. Check version specificity of each source
2. Look for publication or last-updated dates
3. Consider regional or configuration-specific differences
4. When in doubt, prioritize official sources over community content
5. Test critical functionality to verify behavior

### When Documentation Is Unclear
1. Look for examples or tutorials that illustrate the concept
2. Check related documentation for context
3. Consider searching for community explanations or examples
4. Document assumptions made due to ambiguity
5. Plan for validation testing to confirm understanding

## Best Practices for Context7 Queries

### Effective Question Formulation
- Be specific about what you need to know
- Include relevant context (language, version, use case)
- Use correct terminology from the domain
- Ask one focused question rather than multiple broad ones
- Include error messages or specific behaviors when troubleshooting

### Result Evaluation
- Check the source and date of the documentation
- Look for version indicators in the content
- Verify code examples are complete and runnable
- Check for accompanying explanations or warnings
- Consider the reputation and authority of the source

### Documentation Integration
- Cross-reference multiple sources when possible
- Test critical information in a safe environment before production use
- Document where you found information for future reference
- Note any assumptions or interpretations made
- Update knowledge as new versions are released

## Security and Privacy Considerations

### Data Handling
- Remember that queries to Context7 may be logged
- Avoid including sensitive information (API keys, passwords) in queries
- Use generic examples when discussing specific implementations
- Consider implications for proprietary or confidential technologies

### Intellectual Property
- Respect licensing and usage terms of documented technologies
- Provide attribution when using code examples from documentation
- Verify that usage complies with open-source licenses
- Be aware of any restrictions on redistribution or modification

## Performance Optimization

### Efficient Querying
- Batch related questions when possible
- Cache frequently accessed documentation locally
- Use specific, targeted queries rather than broad searches
- Leverage context from previous interactions to refine searches
- Prefetch documentation for known upcoming needs

### Resource Management
- Monitor response times for different types of queries
- Consider prefetching during off-peak hours for anticipated needs
- Implement fallback mechanisms for when documentation is unavailable
- Regularly review and prune cached documentation to manage storage

## Integration with Other MCP Servers

### With Vibe Trading MCP
- Use Context7 to understand quantitative libraries before applying them with Vibe Trading
- Validate financial modeling approaches against documented best practices
- Check implementation details of statistical methods used in trading strategies

### With GitHub MCP
- Compare documented approaches with actual implementation in code repositories
- Use Context7 to verify that repository examples follow official guidelines
- Check for deviations from documented best practices in code reviews
- Validate pull request changes against official documentation

### With Filesystem MCP
- Save critical documentation for offline reference
- Organize downloaded documentation by project or technology
- Maintain version-specific documentation archives
- Create quick-reference guides based on frequently accessed documentation

### With Memory MCP
- Cache frequently referenced documentation snippets
- Store solutions to common problems discovered through documentation
- Maintain a personal knowledge base of verified information
- Track expiration or validity periods for cached documentation

## Maintenance and Updates

### Regular Documentation Reviews
- Schedule quarterly reviews of critical dependency documentation
- Check for major version releases and breaking changes
- Update internal guidelines based on new documentation
- Retire outdated practices as documented in newer versions

### Version Tracking
- Maintain a register of versions for critical dependencies
- Subscribe to release announcements for key technologies
- Document compatibility matrices between different component versions
- Plan upgrades based on documented deprecation timelines

### Feedback Contribution
- Consider contributing to documentation when you identify gaps
- Report errors or inaccuracies found in documentation
- Share useful patterns or implementations that could benefit documentation
- Participate in community efforts to improve documentation quality

## Conclusion

Effective use of Context7 MCP requires discipline, curiosity, and verification. By making documentation consultation a standard part of the workflow, AI agents can significantly reduce errors, improve implementation quality, and stay current with evolving best practices. The key is to treat documentation not as a reference to consult only when stuck, but as the primary source of truth that informs all technical decisions.

Regular practice with these workflows will make documentation consultation a natural and efficient part of the development process, leading to more reliable, maintainable, and high-quality outcomes in the trading bot project.
@cap_mcp
Feature: IDEs and agents reach Ananke over MCP
  Background:
    Given a fresh project
    And I have run "ananke init"

  Scenario: The stdio server lists its read-only tools
    When I run "ananke serve-mcp --project ." with input:
      """
      {"action": "tools.list"}
      {"action": "shutdown"}
      """
    Then it succeeds
    And the output contains each of:
      """
      ananke.verify.run
      ananke.spec.validate
      registry_resolve
      """

 
  Scenario: A standard MCP client can initialise and list tools (JSON-RPC 2.0)
    When I run "ananke serve-mcp --project ." with input:
      """
      {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "acceptance", "version": "1"}}}
      {"jsonrpc": "2.0", "method": "notifications/initialized"}
      {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
      """
    Then it succeeds
    And the output contains "protocolVersion"
    And the output contains "ananke-plexus"
    And the output contains "inputSchema"

  Scenario: An MCP client calls a tool and gets structured content back
    When I run "ananke serve-mcp --project ." with input:
      """
      {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "acceptance", "version": "1"}}}
      {"jsonrpc": "2.0", "method": "notifications/initialized"}
      {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "ananke.policy.explain", "arguments": {"stage": "verify"}}}
      {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "ananke.git.commit", "arguments": {"message": "x"}}}
      """
    Then it succeeds
    And the output contains "policy.core.fail-closed"
    And the output contains "--allow-mutations"

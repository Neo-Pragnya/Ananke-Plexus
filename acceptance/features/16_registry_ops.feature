Feature: The registry is portable, inspectable and maintainable
  Background:
    Given a fresh project
    And I have run "ananke init"
    And I have run "ananke registry init"
    And the fixture "skills" is copied to "skills"
    And I have run "ananke registry learn ./skills/good-skill"

  @cap_registry_ops
  Scenario: An exported registry imports into another project
    When I run "ananke registry export -o reg.tar.zst"
    Then it succeeds
    And the output contains "Exported 1 version(s)"
    When I run:
      """
      mkdir other && cd other && git init -q && ananke init >/dev/null && ananke registry init >/dev/null
      ananke registry import ../reg.tar.zst && ananke registry list --json
      """
    Then it succeeds
    And the output contains "ananke://skill/local/good-skill"

  @cap_registry_ops
  Scenario: A backup restores into an empty directory
    When I run "ananke registry backup --output-dir bk && ananke registry restore $(ls bk/registry-backup-* | head -1) --into restored"
    Then it succeeds
    And the file "restored/registry.db" exists
    And the file "restored/policy.toml" exists

  @cap_registry_ops
  Scenario: Reports, the static docs site and garbage collection
    When I run "ananke registry report inventory --format json"
    Then it succeeds
    And the JSON output at "rows.artifact" contains "skill/local/good-skill"
    When I run "ananke registry report licenses"
    Then the output contains "MIT"
    When I run "ananke registry docs build --output regsite"
    Then it succeeds
    And a file matching "regsite/**/*.html" exists
    When I run "ananke registry gc"
    Then it succeeds
    And the output contains "Would delete"
    When I run "ananke registry duplicates"
    Then it succeeds

  @cap_registry_ops
  Scenario: A new version is diffed with a suggested SemVer bump, then unregistered
    When I run "printf 'Extra line.\n' >> skills/good-skill/SKILL.md && ananke registry learn ./skills/good-skill --version 0.2.0 && ananke registry diff good-skill@0.1.0 good-skill@0.2.0"
    Then it succeeds
    And the output contains "Suggested semver"
    When I run "ananke registry unregister good-skill@0.2.0"
    Then it succeeds
    And the output contains "Archived"

  @cap_registry_ops
  Scenario: Register, activate and deactivate a version; skill shortcuts work
    When I run "ananke registry register ./skills/trap-skill"
    Then it succeeds
    And the path "$SANDBOX/TRAP_EXECUTED" does not exist anywhere
    When I run "ananke registry activate good-skill@0.1.0"
    Then it succeeds
    And the output contains "Activated"
    When I run "ananke registry deactivate good-skill@0.1.0"
    Then it succeeds
    When I run "ananke skill show good-skill && ananke skill search summarise && ananke skill resolve good-skill && ananke agent search --capability release"
    Then it succeeds
    And the output contains "ananke://skill/local/good-skill@0.1.0"

  @cap_signing
  Scenario: A version signed with a trusted key carries a signature
    When I run "ananke registry key generate --signer qa --trust"
    Then it succeeds
    And the file ".ananke/secrets/registry-signing.key" exists
    When I run "git check-ignore .ananke/secrets/registry-signing.key"
    Then it succeeds
    When I run "ananke registry key list"
    Then the output contains "qa"
    When I run "ananke registry sign good-skill@0.1.0 --key .ananke/secrets/registry-signing.key && ananke registry signatures good-skill@0.1.0 --json"
    Then it succeeds
    And the output contains "ed25519"

  @cap_apm
  Scenario: apm searches the registry and locks nothing when nothing is declared
    When I run "apm search summarise --json"
    Then it succeeds
    And the JSON output at "name" contains "good-skill"
    When I run "apm lock"
    Then it fails cleanly
    And the output contains "nothing to sync"
    When I run "ananke sync --dry-run"
    Then it fails cleanly

@cap_apm
Feature: apm installs and audits agent packages
  Background:
    Given a fresh project
    And I have run "ananke init"
    And I have run "ananke registry init"
    And the fixture "skills" is copied to "skills"
    And I have run "ananke registry learn ./skills/good-skill"

  Scenario: A registry skill is installed into the project and verified
    When I run "apm install good-skill@0.1.0"
    Then it succeeds
    When I run "apm list --json"
    Then it succeeds
    And the JSON output at "installed" has at least 1 items
    When I run "apm verify"
    Then it succeeds

  Scenario: An installed skill reports its registry details
    Given I have run "apm install good-skill@0.1.0"
    When I run "apm info good-skill"
    Then it succeeds
    And the output contains "good-skill"

  @cap_cli_ux
  Scenario: Auditing a file that is not an APM manifest is a clean error
    When I run "apm audit --manifest skills/good-skill/SKILL.md"
    Then it fails cleanly

  Scenario: A bundle exported from one project installs into another
    Given I have run "apm install --source $REPO/tests/fixtures/sample-skill"
    And I have run "apm bundle export --name graph-reviewer@1.0.0 --output $SANDBOX/team.tar.gz"
    When I run "mkdir -p $SANDBOX/other && cd $SANDBOX/other && git init -q && apm bundle install --bundle $SANDBOX/team.tar.gz"
    Then it succeeds
    And the output contains "graph-reviewer@1.0.0"

  @known_issue
  Scenario: A skill installed from the registry can be shared as a bundle
    # Registry installs carry ananke.registry.json, bundles expect ananke-skill.toml: install says "missing manifest".
    Given I have run "apm install good-skill@0.1.0"
    And I have run "apm bundle export --name local.good-skill@0.1.0 --output $SANDBOX/team.tar.gz"
    When I run "mkdir -p $SANDBOX/other && cd $SANDBOX/other && git init -q && apm bundle install --bundle $SANDBOX/team.tar.gz"
    Then it succeeds

  @cap_apm_security @adversarial
  Scenario: A bundle whose files point outside the project is refused before anything is written
    When I run:
      """
      python - <<'PY'
      import io, json, tarfile
      with tarfile.open("evil.tar.gz", "w:gz") as a:
          for name, data in {"ananke-bundle.json": json.dumps({"members": ["ok"]}).encode(),
                             "../../escaped.txt": b"pwned"}.items():
              info = tarfile.TarInfo(name); info.size = len(data); a.addfile(info, io.BytesIO(data))
      PY
      apm bundle install --bundle evil.tar.gz
      """
    Then the exit code is 2
    And the output contains "Unsafe bundle member"
    And it does not crash
    And the path "$SANDBOX/escaped.txt" does not exist anywhere

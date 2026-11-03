Feature: The code graph covers notebooks and data, and products read it within a budget
  Background:
    Given a fresh project
    And I have run "ananke init"
    And the file "pkg/__init__.py" with content:
      """
      """
    And the file "pkg/core.py" with content:
      """
      import pandas as pd
      def load():
          return pd.read_csv("data/in.csv")
      """
    And the file "data/in.csv" with content:
      """
      a
      1
      """
    And the file "nb/study.ipynb" with content:
      """
      {"nbformat": 4, "metadata": {}, "cells": [
        {"cell_type": "code", "source": ["%matplotlib inline\n", "from pkg.core import load\n"]},
        {"cell_type": "code", "source": ["def helper():\n", "    return load()\n"]},
        {"cell_type": "code", "source": ["open('ran.txt', 'w').write('x')\n"]},
        {"cell_type": "code", "source": ["%%bash\n", "touch ran-too.txt\n"]}]}
      """
    And I have run "git add -A && git -c user.name=t -c user.email=t@example.com commit -qm init"
    And I have run "ananke app run graph.build --key g"

  @cap_graph
  Scenario: A notebook's symbols cite their cell, and nothing in it runs
    When I run "ananke app code --query helper"
    Then it succeeds
    And the JSON output at "nodes[0].source_location" equals "nb/study.ipynb#cell=2:1"
    And the file "ran.txt" does not exist
    And the file "ran-too.txt" does not exist

  @cap_graph
  Scenario: Data files appear with the code that reads them, and a change reaches the reader
    When I run "ananke app code --kind data"
    Then it succeeds
    And the JSON output at "nodes[0].node_id" equals "data:data/in.csv"
    When I run "ananke graph impact --changed-file data/in.csv"
    Then it succeeds
    And the output contains "pkg/core.py"

  @cap_graph
  Scenario: Collapsing sums edges between groups and accounts for the rest
    When I run "ananke app code --collapse directory"
    Then it succeeds
    And the output contains each of:
      """
      "node_id": "dir:nb"
      "node_id": "dir:pkg"
      "source": "dir:nb"
      "edge_accounting"
      """

  @cap_graph @adversarial
  Scenario: A budget is honoured and bad requests are refused
    When I run "ananke app code --budget 1"
    Then it succeeds
    And the JSON output at "truncated" equals true
    When I run "ananke app code --budget 0"
    Then the exit code is 2
    And the output contains "INVALID_REQUEST"
    When I run "ananke app code --collapse banana"
    Then the exit code is 2
    And the output contains "INVALID_REQUEST"

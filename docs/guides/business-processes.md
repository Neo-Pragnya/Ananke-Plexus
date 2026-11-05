---
title: Business processes
description: Turn a reviewed process document into a model and BPMN 2.0, with every step cited, and nothing guessed or run.
---

# Business processes

Write a process as a numbered list, with each step naming who does it:

```markdown
# Order handling

1. Sales: Receive the order
2. Finance: Is the credit good?
   - Yes: go to step 3
   - No: go to step 5
3. Warehouse: Pick and pack
4. Warehouse: Ship the order. End.
5. Sales: Tell the customer
```

```bash
ananke process extract process.md --bpmn process.bpmn
```

| You write | You get |
| --- | --- |
| `1. Sales: Receive the order` | A task in the Sales lane |
| A step ending in `?`, with `- Yes: go to step 3` beneath it | A decision, with one labelled link for each line |
| `Go to step 4` at the end of a step | A link to that step, rather than the next one |
| `End.` at the end of a step, or `- No: end` | An end event |

Each step goes to the next one unless it says otherwise, and the last step ends.

## Only what it says

Every lane, task and decision cites its line. If the text doesn't hold together, Ananke says so and exits with 1, rather than repairing it:

- a step going to one that doesn't exist;
- a step nothing leads to, or one that can't reach an end;
- a decision with fewer than two ways out.

## Editing it elsewhere

The BPMN is standard 2.0 with a diagram, so tools like Camunda Modeler or bpmn.io can open it. After someone edits it, check it:

```bash
ananke process check process.bpmn                      # IDs, links, reachability
ananke process check process.bpmn --against process.md # does it still say what the document says?
```

A file declaring a DOCTYPE or entities isn't read, because BPMN doesn't need them and they can attack a parser.

## Nothing runs

The process is never marked executable, and Ananke doesn't run it. It's a description to review. To see it as a swimlane diagram, use `dev bpmn` in [Dev Guru](https://pypi.org/project/dev-guru/).

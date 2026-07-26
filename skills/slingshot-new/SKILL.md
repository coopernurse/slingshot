---
name: slingshot-new
description: File a slingshot specification as a GitHub issue
disable-model-invocation: true
---

## Skill: slingshot-new

File a spec as a slingshot GitHub issue, kicking off the agentic workflow.

### When to use

Use after producing a finalized markdown specification for a feature or change
(typically via a `/grill-me` session or other collaborative spec-writing).

Do NOT use for general issue filing or non-slinshot workflows.

### How it works

The skill runs `slingshot new` with the spec file and target repo. If
successful, it prints the URL of the created GitHub issue.

### Usage

```
/slingshot-new --spec <filepath> --repo <owner/name> [--title <title>]
```

**Arguments:**

| Argument   | Required | Description |
|------------|----------|-------------|
| `--spec`   | Yes      | Path to the markdown spec file |
| `--repo`   | Yes      | GitHub repo as `owner/name` |
| `--title`  | No       | Issue title (defaults to the first H1 in the spec) |

### Instructions for the agent

1. Confirm the spec file exists and has been finalized by the user.
2. Run: `slingshot new --spec <file> --repo <owner/name> [--title <title>]`
3. Report the created issue URL to the user.
4. Remind the user to start the daemon if not already running: `slingshot daemon`

### Requirements

- `slingshot` must be on PATH (e.g. installed via `pipx install .` from the slingshot repo).
- `gh` must be authenticated and on PATH.
- The user must have `triage` or higher permissions on the target repo (to create labels).

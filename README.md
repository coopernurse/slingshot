# Slingshot

An agentic coding workflow driven by GitHub issues and pull requests. File a spec
as a GitHub issue, and the daemon feeds it to an agent (opencode) through
**implement → review → approved** cycles until the PR passes review. You merge
the approved PR yourself — v1 has no auto-merge.

## Prerequisites

- **Python 3.11+**
- **[gh](https://cli.github.com/)** — authenticated (`gh auth login`) and on PATH
- **[git](https://git-scm.com/)** — on PATH
- **[opencode](https://opencode.ai)** — on PATH (the agent that runs implement and review prompts)

## Install slingshot

### Option A: pipx editable (recommended)

[pipx](https://pipx.pypa.io/) installs the package in an isolated environment
and makes the `slingshot` command globally available. The `-e` flag registers a
pointer back to this working directory, so source changes take effect
immediately — no reinstall needed.

```bash
pipx install -e .
```

### Option B: pipx (static)

Installs a static copy into pipx's venv. Changes to this working directory are
**not** picked up — re-run `pipx install .` (or `pipx upgrade slingshot`) after
each change.

```bash
pipx install .
```

### Option C: manual symlink (no pipx)

If you prefer not to use pipx, create a venv and symlink the script:

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
ln -s "$(pwd)/.venv/bin/slingshot" /usr/local/bin/slingshot
```

Replace `/usr/local/bin` with any directory on your PATH (e.g. `~/.local/bin`).

## Development

```bash
pip install -e .[dev]
make check
```

## Install the `/slingshot-new` skill for opencode

The repo ships a skill that lets opencode file specs as slingshot issues.
Symlink it into opencode's skills directory:

```bash
mkdir -p ~/.config/opencode/skills
ln -s "$(pwd)/skills/slingshot-new" ~/.config/opencode/skills/slingshot-new
```

After restarting opencode, `/slingshot-new` will be available as a command.

## Configuration

Create `~/.config/slingshot/config.toml`:

```toml
[[repo]]
name = "owner/name"
path = "/Users/you/src/name"
```

That's the minimum. Every field has a sensible default — see the annotated
example below for the full set:

```toml
poll_interval_seconds   = 60    # how often to poll GitHub
claim_timeout_minutes   = 30    # stale-claim reaper threshold
agent_timeout_minutes   = 30    # per-run agent kill switch
review_fail_threshold   = 5     # fail-marker count on PR → blocked
agent_failure_threshold       = 3     # consecutive failures on issue → blocked
unknown_mergeable_threshold    = 5     # max cycles with unknown PR merge status
max_concurrent                = 2     # simultaneous agent runs across all repos

[agent]
implement_command = "opencode run --auto {prompt_file}"
review_commands = ["opencode run --auto {prompt_file}"]

[[repo]]
name = "owner/name"
path = "/Users/you/src/name"

# [[repo]]
# name = "owner/other"
# path = "/Users/you/src/other"
```

### Specifying the agent and model

The `[agent]` section controls how slingshot invokes opencode for implement and
review phases. The `{prompt_file}` placeholder is automatically replaced with the
path to the rendered prompt.

#### Use a specific model

Use `--model` with the `provider/model` format (see `opencode models` to list
available models):

```toml
[agent]
implement_command = "opencode run --auto --model anthropic/claude-sonnet-4-20250514 {prompt_file}"
review_commands = ["opencode run --auto --model openai/gpt-5.1 {prompt_file}"]
```

#### Use a specific opencode agent

OpenCode agents define a system prompt, tool permissions, and optional model
binding. List agents with `opencode agent list`:

```toml
[agent]
implement_command = "opencode run --auto --agent implementer {prompt_file}"
review_commands = ["opencode run --auto --agent reviewer {prompt_file}"]
```

#### Use a different backend

Swap `opencode` for another agent backend entirely — slingshot just runs the
shell command:

```toml
[agent]
implement_command = "claude --auto --print {prompt_file}"
review_commands = ["claude --auto --print {prompt_file}"]
```

#### Useful opencode `run` flags

| Flag | Purpose |
|---|---|
| `--model provider/model` | Select a specific model |
| `--agent name` | Use a named opencode agent |
| `--variant variant` | Provider-specific reasoning effort |
| `--auto` | Auto-approve permissions |
| `--thinking` | Include thinking blocks in output |

## Usage

### File a spec as an issue

```bash
slingshot new --spec spec.md --repo owner/name
```

The title defaults to the first `# Heading` in the spec file. Override it with `--title`.

### `/slingshot-new` (from inside opencode)

```
/slingshot-new --spec path/to/spec.md --repo owner/name
```

### Start the daemon

```bash
slingshot daemon
```

The daemon polls GitHub for issues labeled `slingshot:implement` or
`slingshot:review`, claims them, and drives the agent through the workflow.

```bash
# Run one cycle and exit (useful for testing)
slingshot daemon --once

# Use a non-default config path
slingshot daemon --config /path/to/config.toml
```

## Workflow states

| Label | Meaning |
|---|---|
| `slingshot:implement` | Ready for agent to implement |
| `slingshot:implementing` | Agent is implementing |
| `slingshot:review` | PR open; ready for review agent |
| `slingshot:reviewing` | Agent is reviewing |
| `slingshot:awaiting-checks` | Review passed but CI checks are pending |
| `slingshot:approved` | Review passed — **you merge the PR** |
| `slingshot:blocked` | Agents couldn't converge; human needed |

## Human review comments

Prefix a top-level PR comment or review-thread comment with `/slingshot` to
drive agent rework.  The daemon picks up qualifying comments and feeds them
back to the implement agent as structured rework items.

### Qualifying comments

A comment creates a qualifying item when ALL of:
- Body starts with `/slingshot` (case-sensitive, after trimming).
- Author's `authorAssociation` is `OWNER`, `MEMBER`, or `COLLABORATOR`.
- Top-level (conversation comment or initial review-thread comment — replies
  are ignored).
- Not authored by the daemon's own GitHub user.

Two kinds:
- **Inline** — PR review-thread comment (has a file path and line).  Blocks
  approval until you resolve the thread in GitHub.
- **Conversation** — PR conversation comment (no file location).
  Fire-and-forget: stops blocking once the daemon replies with the agent's
  disposition.

### Resolution workflow

1. You write `/slingshot ...` on the PR.
2. The daemon detects it, bounces the issue back to `slingshot:implement`,
   and the implement agent addresses each item.
3. The daemon replies on each item with the agent's disposition and a hidden
   addressed marker.
4. The review agent verifies your items against the diff.  Items it judges
   still unsolved get a disputed marker and return to the implement agent.

Only you can resolve inline threads (GitHub's "Resolve conversation" button).
The daemon never touches resolution state.

Addressed-but-unresolved threads **never** cause the daemon to bounce back to
implement — you are the final gate.

### Configuring the debounce

```toml
comment_debounce_seconds = 180   # seconds before a qualifying comment
                                 # triggers a bounce (prevents rapid-fire)
```

Defaults to 180 seconds.

## Security note

The daemon executes LLM-interpreted instructions derived from issue content on
your local machine. The security boundary is GitHub's permission model: only
collaborators can apply labels, and `/slingshot` comment authors are
filtered by `authorAssociation` (OWNER, MEMBER, COLLABORATOR) — the same trust
model as labels. **Only run slingshot against repos where every collaborator
is trusted.** v1 adds no further allowlisting or sandboxing.

## License

MIT — see [LICENSE](./LICENSE).

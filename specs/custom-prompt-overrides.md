# Custom Prompt Overrides

Allow users to override the default implementer and reviewer system prompts
via paths to markdown files in the config file. This config is optional — by
default, the hardcoded prompts are used as today.

## Config schema

Two new optional fields in `[agent]` and `[[repo]]`:

```toml
[agent]
implement_command = "opencode run --auto {prompt_file}"
review_commands = ["opencode run --auto {prompt_file}"]
implement_prompt  = "/path/to/implement.md"   # optional
review_prompt     = "/path/to/review.md"       # optional

[[repo]]
name              = "owner/name"
path              = "/Users/you/src/name"
implement_prompt  = "/path/to/per-repo-impl.md"  # optional, overrides global
review_prompt     = "/path/to/per-repo-rev.md"    # optional, overrides global
```

### Precedence

```
per-repo prompt path → global [agent] prompt path → hardcoded constant
```

No per-repo command overrides at this time — only prompts.

### Path resolution

Relative paths are resolved relative to the config file's directory. Absolute
paths are supported.

## Token substitution

User-provided prompt files support `.format()`-style token substitution.
Literal `{` and `}` must be escaped as `{{` and `}}`.

### Tokens

| Token | Implement | Review | Description |
|---|---|---|---|
| `{default_branch}` | Yes | Yes | e.g. `main` |
| `{repo_name}` | Yes | Yes | e.g. `owner/repo` |
| `{issue_number}` | Yes | Yes | GitHub issue number |
| `{spec}` | Yes | Yes | Full issue body |
| `{scenario}` | Yes | — | `"fresh"` / `"resume"` / `"rework"` (empty string on review) |
| `{feedback}` | Yes | — | Reviewer notes (empty string on non-rework) |
| `{worktree_path}` | Yes | Yes | Absolute path to worktree |

### Scope of override

The user file replaces only the **system prompt** string. The render functions
continue to append dynamic context (spec body, scenario label, feedback, worktree
path) below the system prompt in the same structure used today.

No deduplication is performed if `{spec}` appears in the user's system prompt
and the spec is also appended below — user's choice.

## Validation

- **Fail-fast at startup.** Validate every prompt path across all repos.
- **Collect all failures.** If multiple paths are missing/unreadable, report all
  of them at once before exiting.
- If no custom prompt is specified (field omitted), no validation needed —
  the hardcoded default is used.

## Archival breadcrumb

When a custom prompt is used, the archived prompt file (copied to
`.slingshot/prompts/`) includes a breadcrumb comment at the top:

```
<!-- Custom system prompt sourced from: /path/to/file.md -->
```

## Implementation notes

- New fields in `AgentConfig` dataclass: `implement_prompt: str | None` and
  `review_prompt: str | None` (default to `None`).
- New fields in `RepoConfig` dataclass: `implement_prompt: str | None` and
  `review_prompt: str | None` (default to `None`).
- New function `resolve_prompt_path(path: str, config_dir: Path) -> Path` in
  `config.py`.
- New function `validate_prompt_paths(config: Config) -> list[str]` that returns
  a list of error messages (empty if all valid).
- `load_config()` calls `validate_prompt_paths()` and prints errors + exits if
  any are found.
- Token substitution uses a dict of available values passed to `.format()`.
- `render_implement_prompt()` and `render_review_prompt()` accept an optional
  `custom_system_prompt: str | None` parameter.
- The daemon resolves the effective prompt path using repo override first, then
  global, then `None`.
- The token list and escaping rules must be documented in the README.

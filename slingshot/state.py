"""Label-based state machine for the slingshot workflow."""

from __future__ import annotations

AWAITING_CHECKS = "slingshot:awaiting-checks"

SLINGSHOT_LABELS = [
    "slingshot:implement",
    "slingshot:implementing",
    "slingshot:review",
    "slingshot:reviewing",
    "slingshot:approved",
    "slingshot:blocked",
    AWAITING_CHECKS,
]

# Pre-claim states that the daemon polls for.
WORK_STATES = {"slingshot:implement", "slingshot:review"}

# In-flight states (claimed, agent running).
IN_FLIGHT_STATES = {"slingshot:implementing", "slingshot:reviewing"}

# States the review-items watcher checks each poll cycle.
# slingshot:awaiting-checks is non-work, non-flight — never claimed, never reaped.
WATCHER_STATES = {
    "slingshot:review",
    "slingshot:approved",
    "slingshot:blocked",
    AWAITING_CHECKS,
}

# Terminal / non-work states — daemon watches these but never claims them.
# slingshot:awaiting-checks: review passed but checks are pending; watcher
#   polls for check completion.
# slingshot:approved: review passed, checks green, ready for human merge.
#   Watched for CI regressions and new /slingshot comments.
# slingshot:blocked: agents couldn't converge. Watched for new /slingshot
#   comments to unblock.
TERMINAL_STATES = {
    "slingshot:approved",
    "slingshot:blocked",
    AWAITING_CHECKS,
}

# Map in-flight → pre-claim (for reaper / back-off).
FLIGHT_TO_PRECLAIM = {
    "slingshot:implementing": "slingshot:implement",
    "slingshot:reviewing": "slingshot:review",
}

# Map work → in-flight.
WORK_TO_FLIGHT = {
    "slingshot:implement": "slingshot:implementing",
    "slingshot:review": "slingshot:reviewing",
}

# Map work → next state on success.
WORK_TO_SUCCESSOR = {
    "slingshot:implement": "slingshot:review",
    "slingshot:review": "slingshot:approved",
}

SLINGSHOT_LABEL_PREFIX = "slingshot:"


def strip_slingshot_labels(labels: set[str]) -> set[str]:
    """Return *labels* with all slingshot: labels removed."""
    return {lb for lb in labels if not lb.startswith(SLINGSHOT_LABEL_PREFIX)}

from __future__ import annotations

from slingshot import state


class TestStripSlingshotLabels:
    def test_removes_all_slingshot_labels(self):
        result = state.strip_slingshot_labels({
            "slingshot:implement", "bug", "slingshot:review", "enhancement",
        })
        assert result == {"bug", "enhancement"}

    def test_leaves_non_slingshot_labels_untouched(self):
        result = state.strip_slingshot_labels({"bug", "feature", "docs"})
        assert result == {"bug", "feature", "docs"}

    def test_handles_empty_set(self):
        result = state.strip_slingshot_labels(set())
        assert result == set()

    def test_handles_only_slingshot_labels(self):
        result = state.strip_slingshot_labels({"slingshot:implement"})
        assert result == set()


class TestStateMachineInvariants:
    def test_flight_to_preclaim_keys_are_in_flight_states(self):
        for key in state.FLIGHT_TO_PRECLAIM:
            assert key in state.IN_FLIGHT_STATES

    def test_flight_to_preclaim_values_are_work_states(self):
        for val in state.FLIGHT_TO_PRECLAIM.values():
            assert val in state.WORK_STATES

    def test_work_to_flight_keys_are_exactly_work_states(self):
        assert set(state.WORK_TO_FLIGHT.keys()) == state.WORK_STATES

    def test_work_to_successor_keys_are_exactly_work_states(self):
        assert set(state.WORK_TO_SUCCESSOR.keys()) == state.WORK_STATES

    def test_work_to_flight_values_are_exactly_in_flight_states(self):
        assert set(state.WORK_TO_FLIGHT.values()) == state.IN_FLIGHT_STATES

    def test_slingshot_labels_is_disjoint_union(self):
        expected = state.WORK_STATES | state.IN_FLIGHT_STATES | state.TERMINAL_STATES
        assert set(state.SLINGSHOT_LABELS) == expected
        assert len(state.SLINGSHOT_LABELS) == len(expected)

    def test_awaiting_checks_in_labels(self):
        assert state.AWAITING_CHECKS in state.SLINGSHOT_LABELS

    def test_awaiting_checks_in_terminal(self):
        assert state.AWAITING_CHECKS in state.TERMINAL_STATES

    def test_awaiting_checks_in_watcher_states(self):
        assert state.AWAITING_CHECKS in state.WATCHER_STATES

    def test_watcher_states_never_claimed(self):
        # Watcher states can overlap with work states (review is both).
        # Non-work watcher states must not be in work or in-flight sets.
        non_work_watchers = state.WATCHER_STATES - state.WORK_STATES
        for s in non_work_watchers:
            assert s not in state.IN_FLIGHT_STATES

    def test_watcher_states_are_subset_of_labels(self):
        for s in state.WATCHER_STATES:
            assert s in state.SLINGSHOT_LABELS

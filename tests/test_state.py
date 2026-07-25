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

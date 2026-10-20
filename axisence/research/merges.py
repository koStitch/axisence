"""Resolve merged competitors to the competitors that received them, at read time.

Research rows keep the competitor they were recorded for. A retired competitor's
merged_into is never rewritten, so after A is merged into B and B into C, A still
points at B: the end of a chain is the first competitor that is not retired.
"""

from collections.abc import Mapping

from axisence.providers.models import Competitor


class MergeChains:
    """Where every retired competitor's merges lead, loaded in one query."""

    def __init__(self, receiver_by_retired_id: Mapping[int, int]) -> None:
        self._receiver_by_retired_id = dict(receiver_by_retired_id)
        self._member_ids_by_current_id: dict[int, frozenset[int]] | None = None

    @classmethod
    def load(cls) -> MergeChains:
        return cls(
            dict(
                Competitor.objects.filter(retired_at__isnull=False).values_list(
                    "pk", "merged_into_id"
                )
            )
        )

    def current_id(self, competitor_id: int) -> int:
        """Return the competitor at the end of the competitor's merge chain."""
        current_id = competitor_id
        # A chain visits each retired competitor at most once.
        for _ in range(len(self._receiver_by_retired_id) + 1):
            receiver_id = self._receiver_by_retired_id.get(current_id)
            if receiver_id is None:
                return current_id
            current_id = receiver_id
        raise RuntimeError(f"The merge chain of competitor {competitor_id} is broken.")

    def member_ids(self, current_id: int) -> frozenset[int]:
        """Return the competitor and every retired competitor whose chain ends at it."""
        if self._member_ids_by_current_id is None:
            retired_ids_by_current_id: dict[int, set[int]] = {}
            for retired_id in self._receiver_by_retired_id:
                retired_ids_by_current_id.setdefault(
                    self.current_id(retired_id), set()
                ).add(retired_id)
            self._member_ids_by_current_id = {
                receiving_id: frozenset(retired_ids)
                for receiving_id, retired_ids in retired_ids_by_current_id.items()
            }
        return frozenset({current_id}) | self._member_ids_by_current_id.get(
            current_id, frozenset()
        )

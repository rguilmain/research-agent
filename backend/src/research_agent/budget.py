"""Per-run budget reservations against the shared daily limit.

Amounts are conservative local estimates, never a provider billing guarantee. A reservation
is the worst case for one operation; settlement replaces it with what we believe was spent.
"""

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Protocol
from uuid import UUID

__all__ = ["BudgetExhausted", "BudgetLedger", "BudgetStore"]


class BudgetExhausted(Exception):
    """The day's reserved and settled spend leaves no room for one more paid operation."""


class BudgetStore(Protocol):
    """The repository operations this module needs; keeps fakes type-checkable."""

    async def reserve_budget(
        self, run_id: UUID | None, kind: str, amount: Decimal, limit: Decimal, ttl: timedelta
    ) -> UUID: ...

    async def settle_budget(
        self, reservation_id: UUID, actual: Decimal, status: str = "settled"
    ) -> None: ...


@dataclass
class BudgetLedger:
    """One run's view of the shared daily budget."""

    store: BudgetStore
    run_id: UUID | None
    limit: Decimal
    ttl: timedelta

    async def reserve(self, kind: str, amount: Decimal) -> UUID | None:
        """Hold the worst case for one paid operation; free work reserves nothing."""
        if amount <= 0:
            return None
        return await self.store.reserve_budget(self.run_id, kind, amount, self.limit, self.ttl)

    async def settle(self, reservation: UUID | None, actual: Decimal) -> None:
        """Charge a held reservation exactly once; nothing to settle means nothing to do."""
        if reservation is None:
            return
        await self.store.settle_budget(reservation, actual)

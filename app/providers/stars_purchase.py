from dataclasses import dataclass
from typing import Protocol

from app.database.models import Order


@dataclass(frozen=True)
class StarsPurchaseResult:
    succeeded: bool
    pending: bool = False
    uncertain: bool = False
    transaction_id: str | None = None
    request_id: str | None = None
    error_code: str | None = None
    reason: str | None = None


class StarsPurchaseProvider(Protocol):
    provider_name: str

    async def purchase_for_self(self, order: Order) -> StarsPurchaseResult: ...

    async def purchase_as_gift(self, order: Order) -> StarsPurchaseResult: ...


class QueuedStarsPurchaseProvider(Protocol):
    provider_name: str

    async def start_purchase(self, order: Order) -> StarsPurchaseResult: ...

    async def check_purchase(self, request_id: str) -> StarsPurchaseResult: ...


class TestStarsPurchaseProvider:
    provider_name = "test"

    async def purchase_for_self(self, order: Order) -> StarsPurchaseResult:
        return StarsPurchaseResult(succeeded=True, transaction_id=f"test-self-{order.id}")

    async def purchase_as_gift(self, order: Order) -> StarsPurchaseResult:
        return StarsPurchaseResult(succeeded=True, transaction_id=f"test-gift-{order.id}")

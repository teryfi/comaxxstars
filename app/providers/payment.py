from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True)
class PaymentResult:
    payment_id: str
    succeeded: bool
    message: str


class PaymentProvider(Protocol):
    async def create_payment(self, *, order_id: int, amount_rub: Decimal) -> PaymentResult: ...

    async def confirm_payment(self, *, payment_id: str) -> PaymentResult: ...

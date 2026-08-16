from decimal import Decimal

from app.providers.payment import PaymentResult


class TestPaymentProvider:
    async def create_payment(self, *, order_id: int, amount_rub: Decimal) -> PaymentResult:
        return PaymentResult(
            payment_id=f"test-payment-{order_id}",
            succeeded=True,
            message=f"TEST PAYMENT: {amount_rub} RUB",
        )

    async def confirm_payment(self, *, payment_id: str) -> PaymentResult:
        return PaymentResult(
            payment_id=payment_id, succeeded=True, message="TEST payment confirmed"
        )


class DisabledProductionPaymentProvider:
    async def create_payment(self, *, order_id: int, amount_rub: Decimal) -> PaymentResult:
        return PaymentResult(
            payment_id=f"disabled-production-payment-{order_id}",
            succeeded=False,
            message="Real payment provider is not configured",
        )

    async def confirm_payment(self, *, payment_id: str) -> PaymentResult:
        return PaymentResult(
            payment_id=payment_id,
            succeeded=False,
            message="Real payment provider is not configured",
        )

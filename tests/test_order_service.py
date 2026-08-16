from app.domain import OrderKind
from app.services.orders import OrderService


def test_order_idempotency_key_separates_kind_recipient_and_stars() -> None:
    first = OrderService._build_idempotency_key(
        buyer_telegram_id=1,
        kind=OrderKind.SELF,
        recipient_telegram_id=None,
        stars=100,
        request_id="42",
    )
    second = OrderService._build_idempotency_key(
        buyer_telegram_id=1,
        kind=OrderKind.GIFT,
        recipient_telegram_id=2,
        stars=100,
        request_id="42",
    )

    assert first == "1:self:1:100:42"
    assert second == "1:gift:2:100:42"
    assert first != second


def test_order_idempotency_key_can_scope_future_reorders() -> None:
    first = OrderService._build_idempotency_key(
        buyer_telegram_id=1,
        kind=OrderKind.SELF,
        recipient_telegram_id=None,
        stars=100,
        request_id="42",
    )
    second = OrderService._build_idempotency_key(
        buyer_telegram_id=1,
        kind=OrderKind.SELF,
        recipient_telegram_id=None,
        stars=100,
        request_id="43",
    )

    assert first != second

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum


class OrderKind(StrEnum):
    SELF = "self"
    GIFT = "gift"


class OrderStatus(StrEnum):
    CREATED = "created"
    WAITING_FOR_PAYMENT = "waiting_for_payment"
    PAYMENT_DETECTED = "payment_detected"
    PAYMENT_CONFIRMING = "payment_confirming"
    PAID = "paid"
    PURCHASE_PROCESSING = "purchase_processing"
    STARS_SENDING = "stars_sending"
    COMPLETED = "completed"
    PAYMENT_EXPIRED = "payment_expired"
    PAYMENT_FAILED = "payment_failed"
    PURCHASE_FAILED = "purchase_failed"
    REFUND_REQUIRED = "refund_required"
    REFUNDED = "refunded"
    CANCELLED = "cancelled"
    MANUAL_REVIEW = "manual_review"
    WAITING_FOR_MERCHANT_BALANCE = "waiting_for_merchant_balance"


class AdminRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    SUPPORT = "support"


TERMINAL_ORDER_STATUSES = frozenset(
    {
        OrderStatus.COMPLETED,
        OrderStatus.PAYMENT_EXPIRED,
        OrderStatus.PAYMENT_FAILED,
        OrderStatus.REFUNDED,
        OrderStatus.CANCELLED,
    }
)

ALLOWED_ORDER_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.CREATED: frozenset(
        {OrderStatus.WAITING_FOR_PAYMENT, OrderStatus.CANCELLED, OrderStatus.MANUAL_REVIEW}
    ),
    OrderStatus.WAITING_FOR_PAYMENT: frozenset(
        {
            OrderStatus.PAYMENT_DETECTED,
            OrderStatus.PAYMENT_EXPIRED,
            OrderStatus.PAYMENT_FAILED,
            OrderStatus.CANCELLED,
            OrderStatus.MANUAL_REVIEW,
        }
    ),
    OrderStatus.PAYMENT_DETECTED: frozenset(
        {OrderStatus.PAYMENT_CONFIRMING, OrderStatus.MANUAL_REVIEW}
    ),
    OrderStatus.PAYMENT_CONFIRMING: frozenset(
        {OrderStatus.PAID, OrderStatus.PAYMENT_FAILED, OrderStatus.MANUAL_REVIEW}
    ),
    OrderStatus.PAID: frozenset(
        {OrderStatus.PURCHASE_PROCESSING, OrderStatus.REFUND_REQUIRED, OrderStatus.MANUAL_REVIEW}
    ),
    OrderStatus.PURCHASE_PROCESSING: frozenset(
        {
            OrderStatus.STARS_SENDING,
            OrderStatus.PURCHASE_FAILED,
            OrderStatus.REFUND_REQUIRED,
            OrderStatus.MANUAL_REVIEW,
            OrderStatus.WAITING_FOR_MERCHANT_BALANCE,
        }
    ),
    OrderStatus.STARS_SENDING: frozenset(
        {
            OrderStatus.COMPLETED,
            OrderStatus.PURCHASE_FAILED,
            OrderStatus.REFUND_REQUIRED,
            OrderStatus.MANUAL_REVIEW,
            OrderStatus.WAITING_FOR_MERCHANT_BALANCE,
        }
    ),
    OrderStatus.PURCHASE_FAILED: frozenset(
        {OrderStatus.REFUND_REQUIRED, OrderStatus.MANUAL_REVIEW}
    ),
    OrderStatus.REFUND_REQUIRED: frozenset({OrderStatus.REFUNDED, OrderStatus.MANUAL_REVIEW}),
    OrderStatus.PAYMENT_EXPIRED: frozenset({OrderStatus.MANUAL_REVIEW}),
    OrderStatus.CANCELLED: frozenset({OrderStatus.MANUAL_REVIEW}),
    OrderStatus.MANUAL_REVIEW: frozenset(
        {
            OrderStatus.PAID,
            OrderStatus.PURCHASE_PROCESSING,
            OrderStatus.STARS_SENDING,
            OrderStatus.COMPLETED,
            OrderStatus.REFUND_REQUIRED,
            OrderStatus.REFUNDED,
            OrderStatus.CANCELLED,
        }
    ),
    OrderStatus.WAITING_FOR_MERCHANT_BALANCE: frozenset(
        {
            OrderStatus.PURCHASE_PROCESSING,
            OrderStatus.MANUAL_REVIEW,
            OrderStatus.REFUND_REQUIRED,
        }
    ),
}


class CustomerPaymentType(StrEnum):
    TEST = "test"
    TON = "ton"
    YOOKASSA = "yookassa"


class PaymentStatus(StrEnum):
    CREATED = "created"
    PENDING = "pending"
    WAITING_FOR_CAPTURE = "waiting_for_capture"
    DETECTED = "detected"
    CONFIRMING = "confirming"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"
    EXPIRED = "expired"
    MANUAL_REVIEW = "manual_review"


class PurchaseAttemptStatus(StrEnum):
    CREATED = "created"
    SUBMITTING = "submitting"
    QUEUED = "queued"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True)
class StarsOption:
    stars: int
    currency: str
    amount_minor: int
    store_product: str | None = None

    @property
    def usd_price(self) -> Decimal:
        if self.currency.upper() != "USD":
            raise ValueError(f"Unsupported Telegram option currency: {self.currency}")
        return Decimal(self.amount_minor) / Decimal("100")

    @property
    def major_amount(self) -> Decimal:
        return Decimal(self.amount_minor) / Decimal("100")


@dataclass(frozen=True)
class PricedStarsOption:
    option: StarsOption
    usd_rub_rate: Decimal
    markup_percent: Decimal
    rub_amount: Decimal
    unit_price: Decimal | None = None
    unit_currency: str | None = None
    provider_commission_percent: Decimal = Decimal("0")
    quote_expires_at: datetime | None = None


def validate_stars_amount(stars: int, *, minimum: int, maximum: int) -> int:
    if isinstance(stars, bool) or not isinstance(stars, int):
        raise ValueError("Stars amount must be an integer")
    if stars < minimum:
        raise ValueError(f"Minimum Stars amount is {minimum}")
    if stars > maximum:
        raise ValueError(f"Maximum Stars amount is {maximum}")
    return stars


def parse_stars_amount(value: str | None, *, minimum: int, maximum: int) -> int:
    if value is None:
        raise ValueError("Stars amount is required")
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > len(str(maximum))
        or not normalized.isascii()
        or not normalized.isdecimal()
    ):
        raise ValueError("Stars amount must contain ASCII digits only")
    return validate_stars_amount(int(normalized), minimum=minimum, maximum=maximum)


def ensure_order_transition(current: OrderStatus, target: OrderStatus) -> None:
    if current == target:
        return
    if target not in ALLOWED_ORDER_TRANSITIONS.get(current, frozenset()):
        raise ValueError(f"Invalid order status transition: {current.value} -> {target.value}")


def calculate_rub_price(
    option: StarsOption, usd_rub_rate: Decimal, markup_percent: Decimal
) -> Decimal:
    multiplier = Decimal("1") + markup_percent / Decimal("100")
    currency = option.currency.upper()
    if currency == "RUB":
        value = option.major_amount * multiplier
    elif currency == "USD":
        value = option.usd_price * usd_rub_rate * multiplier
    else:
        raise ValueError(f"Unsupported Telegram option currency: {option.currency}")
    return value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)

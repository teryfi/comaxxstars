from dataclasses import dataclass

from app.config import Settings
from app.database.models import Order
from app.providers.stars_purchase import StarsPurchaseResult
from app.services.fragment_client import FragmentClient, normalize_username

FRAGMENT_MIN_STARS = 50


@dataclass(frozen=True)
class FragmentStarsPurchaseProvider:
    settings: Settings
    client: FragmentClient | None = None

    provider_name = "fragment"

    def __post_init__(self) -> None:
        if self.client is None:
            object.__setattr__(self, "client", FragmentClient(self.settings))

    async def start_purchase(self, order: Order) -> StarsPurchaseResult:
        username = order.buyer_username if order.kind.value == "self" else order.recipient_username
        if not username:
            return StarsPurchaseResult(
                succeeded=False,
                error_code="RECIPIENT_USERNAME_MISSING",
                reason="Recipient username is missing",
            )
        if not self.settings.real_stars_purchase_enabled:
            return StarsPurchaseResult(
                succeeded=False,
                error_code="REAL_PURCHASE_DISABLED",
                reason="Real Stars purchase is disabled",
            )
        if order.stars < FRAGMENT_MIN_STARS:
            return StarsPurchaseResult(
                succeeded=False,
                error_code="AMOUNT_BELOW_MINIMUM",
                reason=f"Fragment minimum is {FRAGMENT_MIN_STARS} Stars",
            )
        try:
            normalize_username(username)
        except ValueError:
            return StarsPurchaseResult(
                succeeded=False,
                error_code="INVALID_USERNAME",
                reason="Invalid Telegram username",
            )

        result = await self._require_client().start_stars_purchase(
            username=username, amount=order.stars
        )
        return StarsPurchaseResult(
            succeeded=result.success,
            pending=result.pending,
            uncertain=result.uncertain,
            transaction_id=result.transaction_id,
            request_id=result.request_id,
            error_code=result.error_code,
            reason=result.error,
        )

    async def check_purchase(self, request_id: str) -> StarsPurchaseResult:
        result = await self._require_client().get_purchase_status(request_id)
        return StarsPurchaseResult(
            succeeded=result.success,
            pending=result.pending,
            uncertain=result.uncertain,
            transaction_id=result.transaction_id,
            request_id=result.request_id,
            error_code=result.error_code,
            reason=result.error,
        )

    async def purchase_for_self(self, order: Order) -> StarsPurchaseResult:
        return await self.start_purchase(order)

    async def purchase_as_gift(self, order: Order) -> StarsPurchaseResult:
        return await self.start_purchase(order)

    def _require_client(self) -> FragmentClient:
        if self.client is None:
            raise RuntimeError("Fragment client did not initialize")
        return self.client

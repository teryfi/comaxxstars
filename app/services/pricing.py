from decimal import Decimal

from app.domain import PricedStarsOption, StarsOption, calculate_rub_price
from app.services.exchange_rate import ExchangeRateService


class PricingService:
    def __init__(
        self, exchange_rate_service: ExchangeRateService, markup_percent: Decimal | float
    ) -> None:
        self.exchange_rate_service = exchange_rate_service
        self.markup_percent = Decimal(str(markup_percent))

    async def price_options(self, options: list[StarsOption]) -> list[PricedStarsOption]:
        usd_rub_rate = await self.exchange_rate_service.get_usd_rub_rate()
        return [
            PricedStarsOption(
                option=option,
                usd_rub_rate=usd_rub_rate,
                markup_percent=self.markup_percent,
                rub_amount=calculate_rub_price(option, usd_rub_rate, self.markup_percent),
            )
            for option in options
        ]

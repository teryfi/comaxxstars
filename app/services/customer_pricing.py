from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

from app.config import Settings
from app.domain import PricedStarsOption, StarsOption, validate_stars_amount
from app.services.exchange_rate import ExchangeRateService
from app.services.fragment_client import FragmentClient, FragmentStarsUnitPrice
from app.services.ton_payment import TonPaymentService


@dataclass(frozen=True)
class TonPaymentQuote:
    ton_amount: Decimal
    usd_amount: Decimal
    rub_amount: Decimal
    unit_price: Decimal
    unit_currency: str
    commission_percent: Decimal
    markup_percent: Decimal
    quote_expires_at: datetime


class CustomerPricingService:
    def __init__(
        self,
        settings: Settings,
        fragment_client: FragmentClient,
        ton_service: TonPaymentService,
        exchange_rate_service: ExchangeRateService,
    ) -> None:
        self.settings = settings
        self.fragment_client = fragment_client
        self.ton_service = ton_service
        self.exchange_rate_service = exchange_rate_service

    async def price_options(self, options: list[StarsOption]) -> list[PricedStarsOption]:
        unit_price, usd_rub_rate, ton_usd_rate = await self._get_live_rates()
        expires_at = self.quote_expires_at()
        return [
            self._priced_option(
                option=option,
                unit_price=unit_price,
                usd_rub_rate=usd_rub_rate,
                ton_usd_rate=ton_usd_rate,
                expires_at=expires_at,
            )
            for option in options
        ]

    async def quote_option(self, stars: int) -> PricedStarsOption:
        validate_stars_amount(
            stars, minimum=self.settings.min_stars, maximum=self.settings.max_stars
        )
        unit_price, usd_rub_rate, ton_usd_rate = await self._get_live_rates()
        option = StarsOption(stars=stars, currency=unit_price.currency, amount_minor=0)
        return self._priced_option(
            option=option,
            unit_price=unit_price,
            usd_rub_rate=usd_rub_rate,
            ton_usd_rate=ton_usd_rate,
            expires_at=self.quote_expires_at(),
        )

    async def quote_ton_payment(self, *, stars: int) -> TonPaymentQuote:
        validate_stars_amount(
            stars, minimum=self.settings.min_stars, maximum=self.settings.max_stars
        )
        unit_price, usd_rub_rate, ton_usd_rate = await self._get_live_rates()
        markup_percent = self._markup_percent_for_stars(stars)
        total_currency = self._with_markup(unit_price.total_for(stars), markup_percent)
        total_usd = self._to_usd(total_currency, unit_price.currency, ton_usd_rate)
        if unit_price.currency == "TON":
            ton_amount = total_currency.quantize(Decimal("0.000000001"), rounding=ROUND_CEILING)
        else:
            ton_amount = await self.ton_service.usd_to_ton(total_usd)
        self._validate_ton_amount(ton_amount)
        return TonPaymentQuote(
            ton_amount=ton_amount,
            usd_amount=total_usd.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            rub_amount=(total_usd * usd_rub_rate).quantize(Decimal("1"), rounding=ROUND_HALF_UP),
            unit_price=unit_price.amount,
            unit_currency=unit_price.currency,
            commission_percent=unit_price.commission_percent,
            markup_percent=markup_percent,
            quote_expires_at=self.quote_expires_at(),
        )

    async def ton_payment_from_quote(
        self,
        *,
        stars: int,
        unit_price: Decimal,
        unit_currency: str,
        commission_percent: Decimal,
        markup_percent: Decimal,
        rub_amount: Decimal,
        quote_expires_at: datetime,
    ) -> TonPaymentQuote:
        """Convert an already-confirmed quote to TON without repricing Stars."""
        validate_stars_amount(
            stars, minimum=self.settings.min_stars, maximum=self.settings.max_stars
        )
        if quote_expires_at <= datetime.now(UTC):
            raise ValueError("Price quote has expired")

        total_currency = unit_price * Decimal(stars)
        total_currency *= Decimal("1") + markup_percent / Decimal("100")
        currency = unit_currency.upper()
        if currency == "TON":
            total_usd = total_currency * await self.ton_service.get_ton_usd_rate()
            ton_amount = total_currency.quantize(Decimal("0.000000001"), rounding=ROUND_CEILING)
        elif currency in {"USD", "USDT"}:
            total_usd = total_currency
            ton_amount = await self.ton_service.usd_to_ton(total_usd)
        else:
            raise ValueError("Unsupported stored quote currency")
        self._validate_ton_amount(ton_amount)

        return TonPaymentQuote(
            ton_amount=ton_amount,
            usd_amount=total_usd.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            rub_amount=rub_amount,
            unit_price=unit_price,
            unit_currency=currency,
            commission_percent=commission_percent,
            markup_percent=markup_percent,
            quote_expires_at=quote_expires_at,
        )

    async def _get_live_rates(self) -> tuple[FragmentStarsUnitPrice, Decimal, Decimal | None]:
        unit_price = await self.fragment_client.get_stars_unit_price()
        usd_rub_rate = await self.exchange_rate_service.get_usd_rub_rate()
        ton_usd_rate = (
            Decimal(str(await self.ton_service.get_ton_usd_rate()))
            if unit_price.currency == "TON"
            else None
        )
        return unit_price, Decimal(str(usd_rub_rate)), ton_usd_rate

    def _priced_option(
        self,
        *,
        option: StarsOption,
        unit_price: FragmentStarsUnitPrice,
        usd_rub_rate: Decimal,
        ton_usd_rate: Decimal | None,
        expires_at: datetime,
    ) -> PricedStarsOption:
        markup_percent = self._markup_percent_for_stars(option.stars)
        total_currency = self._with_markup(unit_price.total_for(option.stars), markup_percent)
        total_usd = self._to_usd(total_currency, unit_price.currency, ton_usd_rate)
        rub_amount = (total_usd * usd_rub_rate).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        if rub_amount <= 0 or rub_amount > self.settings.max_order_rub_amount:
            raise ValueError("Quoted order total is outside configured transaction limits")
        return PricedStarsOption(
            option=option,
            usd_rub_rate=usd_rub_rate,
            markup_percent=markup_percent,
            rub_amount=rub_amount,
            unit_price=unit_price.amount,
            unit_currency=unit_price.currency,
            provider_commission_percent=unit_price.commission_percent,
            quote_expires_at=expires_at,
        )

    def _with_markup(self, amount: Decimal, markup_percent: Decimal) -> Decimal:
        multiplier = Decimal("1") + markup_percent / Decimal("100")
        return amount * multiplier

    def _markup_percent_for_stars(self, stars: int) -> Decimal:
        small_order_markup = self.settings.stars_markup_percent
        standard_order_markup = self.settings.stars_standard_order_markup_percent
        large_order_markup = self.settings.stars_large_order_markup_percent
        if stars <= self.settings.min_stars:
            return small_order_markup
        if standard_order_markup is None and large_order_markup is None:
            return small_order_markup

        standard_threshold = self.settings.stars_standard_order_threshold
        large_threshold = self.settings.stars_large_order_threshold
        if standard_order_markup is not None and stars <= standard_threshold:
            return self._interpolate_markup(
                stars,
                start_stars=self.settings.min_stars,
                start_markup=small_order_markup,
                end_stars=standard_threshold,
                end_markup=standard_order_markup,
            )
        if large_order_markup is None:
            if standard_order_markup is None:
                return small_order_markup
            return standard_order_markup
        if stars >= large_threshold:
            return large_order_markup
        if standard_order_markup is None:
            return self._interpolate_markup(
                stars,
                start_stars=self.settings.min_stars,
                start_markup=small_order_markup,
                end_stars=large_threshold,
                end_markup=large_order_markup,
            )
        return self._interpolate_markup(
            stars,
            start_stars=standard_threshold,
            start_markup=standard_order_markup,
            end_stars=large_threshold,
            end_markup=large_order_markup,
        )

    @staticmethod
    def _interpolate_markup(
        stars: int,
        *,
        start_stars: int,
        start_markup: Decimal,
        end_stars: int,
        end_markup: Decimal,
    ) -> Decimal:
        # Logarithmic interpolation avoids jumps while making equal order-size
        # multipliers (for example 100 -> 1,000 -> 10,000) equally meaningful.
        order_ratio = Decimal(stars) / Decimal(start_stars)
        threshold_ratio = Decimal(end_stars) / Decimal(start_stars)
        discount_progress = order_ratio.ln() / threshold_ratio.ln()
        markup = start_markup + (end_markup - start_markup) * discount_progress
        return markup.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    @staticmethod
    def _to_usd(amount: Decimal, currency: str, ton_usd_rate: Decimal | None) -> Decimal:
        if currency in {"USD", "USDT"}:
            return amount
        if currency == "TON" and ton_usd_rate is not None:
            return amount * ton_usd_rate
        raise ValueError(f"Unsupported Fragment price currency: {currency}")

    def quote_expires_at(self) -> datetime:
        return datetime.now(UTC) + timedelta(seconds=self.settings.quote_ttl_seconds)

    def order_expires_at(self) -> datetime:
        return datetime.now(UTC) + timedelta(minutes=self.settings.order_timeout_minutes)

    def _validate_ton_amount(self, amount: Decimal) -> None:
        if amount <= 0 or amount > self.settings.max_payment_ton:
            raise ValueError("TON payment amount is outside configured transaction limits")

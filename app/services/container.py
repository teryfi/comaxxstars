from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.payments.yookassa import YooKassaClient
from app.providers.fragment_stars import FragmentStarsPurchaseProvider
from app.providers.stars_purchase import StarsPurchaseProvider, TestStarsPurchaseProvider
from app.providers.test_payment import DisabledProductionPaymentProvider, TestPaymentProvider
from app.services.abuse import AbuseGuard
from app.services.customer_pricing import CustomerPricingService
from app.services.exchange_rate import ExchangeRateService
from app.services.fragment_client import FragmentClient
from app.services.order_worker import OrderWorker
from app.services.orders import OrderService
from app.services.payment_monitor import PaymentMonitor
from app.services.pricing import PricingService
from app.services.ton_payment import TonPaymentService
from app.telegram.client import TelegramUserClient


@dataclass(frozen=True)
class Container:
    settings: Settings
    telegram_client: TelegramUserClient
    fragment_client: FragmentClient | None
    pricing_service: PricingService | CustomerPricingService
    order_service: OrderService
    abuse_guard: AbuseGuard
    order_worker: OrderWorker
    payment_monitor: PaymentMonitor | None = None

    @property
    def is_fragment_mode(self) -> bool:
        return self.settings.stars_purchase_provider == "fragment"

    @property
    def is_test_payment(self) -> bool:
        return self.settings.test_payment_mode

    @property
    def is_ton_payment(self) -> bool:
        return self.settings.customer_payment_provider == "ton"

    @property
    def is_yookassa_payment(self) -> bool:
        return self.settings.customer_payment_provider == "yookassa"


def build_container(
    settings: Settings, session_factory: async_sessionmaker[AsyncSession]
) -> Container:
    telegram_client = TelegramUserClient(settings)
    exchange_rate_service = ExchangeRateService()
    pricing_service: PricingService | CustomerPricingService = PricingService(
        exchange_rate_service,
        settings.stars_markup_percent,
    )
    fragment_client = None
    customer_pricing = None
    ton_service = None
    yookassa_client = None

    if settings.stars_purchase_provider == "fragment":
        fragment_client = FragmentClient(settings)
        ton_service = TonPaymentService(
            wallet_address=settings.ton_wallet_address or "",
            toncenter_api_key=settings.secret_value(settings.toncenter_api_key),
            scan_limit=settings.ton_scan_limit,
        )
        customer_pricing = CustomerPricingService(
            settings,
            fragment_client,
            ton_service,
            exchange_rate_service,
        )
        pricing_service = customer_pricing
        payment_provider = (
            TestPaymentProvider()
            if settings.test_payment_mode
            else DisabledProductionPaymentProvider()
        )
        if (
            settings.customer_payment_provider == "yookassa"
            and not settings.test_payment_mode
            and settings.process_role in {"bot", "admin", "gateway"}
        ):
            yookassa_client = YooKassaClient(settings)
    elif settings.test_payment_mode:
        payment_provider = TestPaymentProvider()
    else:
        raise RuntimeError("Non-Fragment real payment mode is not supported")

    def stars_provider_factory() -> StarsPurchaseProvider:
        if settings.stars_purchase_provider == "fragment":
            if settings.real_stars_purchase_enabled:
                if fragment_client is None:
                    raise RuntimeError("Fragment client is not configured")
                return FragmentStarsPurchaseProvider(settings, fragment_client)
            return TestStarsPurchaseProvider()
        if settings.test_payment_mode:
            return TestStarsPurchaseProvider()
        raise RuntimeError("Non-Fragment real Stars purchase is not supported")

    order_service = OrderService(
        settings=settings,
        session_factory=session_factory,
        payment_provider=payment_provider,
        stars_provider_factory=stars_provider_factory,
        customer_pricing=customer_pricing,
        ton_service=ton_service,
        yookassa_client=yookassa_client,
    )
    payment_monitor = (
        PaymentMonitor(settings, session_factory, order_service, ton_service)
        if (
            ton_service is not None
            and not settings.test_payment_mode
            and settings.customer_payment_provider == "ton"
        )
        else None
    )
    return Container(
        settings=settings,
        telegram_client=telegram_client,
        fragment_client=fragment_client,
        pricing_service=pricing_service,
        order_service=order_service,
        abuse_guard=AbuseGuard(
            default_limit=settings.rate_limit_requests,
            window_seconds=settings.rate_limit_window_seconds,
        ),
        order_worker=OrderWorker(
            settings,
            session_factory,
            order_service,
            process_orders=settings.process_role in {"all", "worker"},
        ),
        payment_monitor=payment_monitor,
    )

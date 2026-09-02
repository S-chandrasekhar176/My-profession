from typing import Any, Dict, Optional

from brokers.base import BaseBroker
from brokers.paper_broker import PaperBroker
from brokers.angel_one import AngelOneBroker
from brokers.shoonya import ShoonyaBroker
from brokers.dhan import DhanBroker
from brokers.fyers import FyersBroker
from brokers.kite import KiteBroker
from fees.nse_fee_calculator import NSEFeeCalculator


class BrokerFactory:
    """Factory for creating broker instances.

    Usage:
        broker = BrokerFactory.create('paper', mode='paper', initial_capital=100000)
        broker = BrokerFactory.create('zerodha', mode='live', api_key='...', access_token='...')
        broker = BrokerFactory.create('dhan', mode='live', client_id='...', access_token='...')
        broker = BrokerFactory.create('fyers', mode='live', app_id='...', access_token='...')
    """

    _ALIAS_MAP: Dict[str, str] = {
        'angelone': 'angel_one',
        'angel-one': 'angel_one',
        'angel_one': 'angel_one',
        'shoonya': 'shoonya',
        'finvasia': 'shoonya',
        'zerodha': 'zerodha',
        'kite': 'zerodha',
        'dhan': 'dhan',
        'fyers': 'fyers',
        'paper': 'paper',
        'yahoofinance': 'paper',
        'yahoo_finance': 'paper',
        'yahoo': 'paper',
        'yfinance': 'paper',
        'virtual': 'paper',
        'simulation': 'paper',
        'simulated': 'paper',
        'demo': 'paper',
        'upstox': 'paper',
    }

    _registry: Dict[str, type] = {
        'paper': PaperBroker,
        'angel_one': AngelOneBroker,
        'angelone': AngelOneBroker,
        'shoonya': ShoonyaBroker,
        'dhan': DhanBroker,
        'fyers': FyersBroker,
        'zerodha': KiteBroker,
        'kite': KiteBroker,
    }

    @staticmethod
    def create(broker_name: str, mode: str = 'paper', **kwargs: Any) -> BaseBroker:
        """Create a broker instance.

        Args:
            broker_name: One of 'paper', 'angel_one', 'angelone', 'shoonya', 'dhan', 'fyers', 'zerodha', 'yahoofinance'.
            mode: 'paper' or 'live'.
            **kwargs: Additional kwargs passed to the broker constructor.

        Returns:
            An instance of the requested broker.

        Raises:
            ValueError: If broker_name is not recognized in live mode.
        """
        raw_name = str(broker_name or 'paper').lower().strip().replace('-', '_').replace(' ', '')
        normalized = BrokerFactory._ALIAS_MAP.get(raw_name, raw_name)

        # For paper mode or paper-mapped brokers, always return PaperBroker
        if mode == 'paper' or normalized == 'paper':
            fee_calc = kwargs.pop('fee_calculator', None) or NSEFeeCalculator()
            repo = kwargs.pop('repository', None)
            capital = kwargs.pop('initial_capital', 100000.0)
            # P2-b: production paper fills include realistic slippage
            # (half-spread + size impact) from defaults.yaml. Explicit
            # slippage_config in kwargs wins (tests / overrides).
            slippage_cfg = kwargs.pop('slippage_config', None)
            if slippage_cfg is None:
                try:
                    from config.settings import settings as _settings

                    slippage_cfg = _settings._raw_config.get('paper_broker', {}).get('slippage', {})
                except Exception:
                    slippage_cfg = {}
            return PaperBroker(
                initial_capital=float(capital),
                fee_calculator=fee_calc,
                repository=repo,
                slippage_config=slippage_cfg or {},
            )

        broker_cls = BrokerFactory._registry.get(normalized)
        if broker_cls is None:
            available = ', '.join(BrokerFactory._registry.keys())
            raise ValueError(f"Unknown broker: {broker_name}. Available: {available}")

        if normalized == 'angel_one':
            return AngelOneBroker(**kwargs)

        if normalized == 'shoonya':
            return ShoonyaBroker(**kwargs)

        return broker_cls(**kwargs)

    @staticmethod
    def register(name: str, cls: type) -> None:
        """Register a custom broker class."""
        BrokerFactory._registry[name] = cls

    @staticmethod
    def available_brokers() -> list:
        """Return list of registered broker names."""
        return list(BrokerFactory._registry.keys())

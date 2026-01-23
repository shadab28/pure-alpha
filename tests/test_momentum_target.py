from decimal import Decimal
import sys
import os

# Ensure project root is on sys.path so tests can import application modules
ROOT = os.path.dirname(os.path.dirname(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from Webapp.momentum_strategy import MomentumStrategy


def test_calculate_target_p2_returns_none_when_config_none():
    """Ensure _calculate_target returns None for P2 when POSITION_2_TARGET_PCT is None."""
    # Call as unbound function; method does not use self
    result = MomentumStrategy._calculate_target(None, Decimal('100.00'), 2)
    assert result is None

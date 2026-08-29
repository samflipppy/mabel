"""Billing stubs. Money is NUMERIC(12,2). Never float."""

from decimal import Decimal

PLANS = {
    "nights": Decimal("199.00"),
    "most_trades": Decimal("349.00"),
    "restoration": Decimal("549.00"),
}

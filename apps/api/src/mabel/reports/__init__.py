"""Monthly report. Sums NUMERIC values the owner entered by hand. Never an LLM."""

from decimal import Decimal


def sum_won(amounts: list[Decimal]) -> Decimal:
    total = Decimal("0.00")
    for amount in amounts:
        if not isinstance(amount, Decimal):
            raise TypeError("Mabel only sums amounts the owner entered as money, not guesses.")
        total += amount
    return total.quantize(Decimal("0.01"))

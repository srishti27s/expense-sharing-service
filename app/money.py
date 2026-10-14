

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Iterable


def to_paise(amount: Decimal) -> int:
    try:
        quantized = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise ValueError("amount must be a valid currency amount") from exc
    if quantized != amount:
        raise ValueError("amount must have at most 2 decimal places")
    return int(quantized * 100)


def rupees(paise: int) -> Decimal:
    return (Decimal(paise) / 100).quantize(Decimal("0.01"))


def split_equally(amount_paise: int, user_ids: Iterable[int]) -> dict[int, int]:
    ids = sorted(user_ids)
    if not ids:
        raise ValueError("at least one participant is required")
    base, remainder = divmod(amount_paise, len(ids))
   
    return {user_id: base + (index < remainder) for index, user_id in enumerate(ids)}


def settle(net_by_user: dict[int, int]) -> list[tuple[int, int, int]]:
    """Return minimal greedy transfers (debtor id, creditor id, paise)."""
    debtors = [[user, -balance] for user, balance in sorted(net_by_user.items()) if balance < 0]
    creditors = [[user, balance] for user, balance in sorted(net_by_user.items()) if balance > 0]
    result: list[tuple[int, int, int]] = []
    i = j = 0
    while i < len(debtors) and j < len(creditors):
        amount = min(debtors[i][1], creditors[j][1])
        result.append((debtors[i][0], creditors[j][0], amount))
        debtors[i][1] -= amount
        creditors[j][1] -= amount
        if debtors[i][1] == 0:
            i += 1
        if creditors[j][1] == 0:
            j += 1
    return result

from collections import defaultdict

TOP_HOLDING_MEDIUM = 0.25
TOP_HOLDING_HIGH = 0.40
TOP_THREE_LIMIT = 0.60
MIN_HOLDINGS_LOW = 3
MIN_HOLDINGS_OK = 5
KIND_LIMIT = 0.90


def to_number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def first_present(source, *keys):
    if not isinstance(source, dict):
        return None
    for key in keys:
        if source.get(key) is not None:
            return source[key]
    return None


def read_symbol(raw, instrument):
    symbol = first_present(instrument, "symbol", "raw_symbol", "ticker")
    if symbol is None:
        symbol = first_present(raw, "symbol", "ticker")
    if isinstance(symbol, dict):
        symbol = first_present(symbol, "symbol", "raw_symbol", "ticker")
    if symbol is None:
        symbol = first_present(instrument, "name", "description")
    return str(symbol).strip().upper() if symbol else "UNKNOWN"


def read_currency(raw, instrument):
    currency = first_present(raw, "currency", "price_currency")
    if currency is None:
        currency = first_present(instrument, "currency")
    if isinstance(currency, dict):
        currency = first_present(currency, "code")
    return str(currency).strip().upper() if currency else "UNKNOWN"


def normalize_position(account_id, raw):
    if not isinstance(raw, dict):
        raw = {}
    instrument = raw.get("instrument")
    if not isinstance(instrument, dict):
        instrument = {}
    quantity = to_number(first_present(raw, "quantity", "units", "fractional_units"))
    price = to_number(first_present(raw, "price", "last_price", "current_price"))
    value = to_number(first_present(raw, "market_value", "value", "current_value"))
    if value is None and quantity is not None and price is not None:
        value = quantity * price
    return {
        "account_id": str(account_id),
        "symbol": read_symbol(raw, instrument),
        "kind": str(first_present(instrument, "kind", "type") or first_present(raw, "kind") or "other").lower(),
        "quantity": quantity,
        "price": price,
        "value": value,
        "currency": read_currency(raw, instrument),
    }


def percent(weight):
    return f"{weight * 100:.1f}%"


def empty_result(unvalued, other_currencies):
    return {
        "currency": None,
        "total_value": 0.0,
        "holding_count": 0,
        "top_holding_weight": 0.0,
        "top_three_weight": 0.0,
        "effective_holdings": 0.0,
        "by_kind": [],
        "holdings": [],
        "flags": [
            {
                "id": "no_data",
                "severity": "info",
                "title": "Nothing to analyse",
                "detail": "No positions with a usable market value were found.",
            }
        ],
        "risk_score": 0,
        "level": "unknown",
        "excluded": {"unvalued": unvalued, "other_currencies": other_currencies},
    }


def analyze(positions):
    valued = [p for p in positions if p["value"] is not None and p["value"] > 0]
    unvalued = len(positions) - len(valued)

    totals_by_currency = defaultdict(float)
    for position in valued:
        totals_by_currency[position["currency"]] += position["value"]

    if not valued:
        return empty_result(unvalued, {})

    currency = max(totals_by_currency, key=totals_by_currency.get)
    other_currencies = {
        code: round(amount, 2)
        for code, amount in totals_by_currency.items()
        if code != currency
    }
    main = [p for p in valued if p["currency"] == currency]
    total = sum(p["value"] for p in main)

    merged = {}
    for position in main:
        row = merged.setdefault(
            position["symbol"],
            {"symbol": position["symbol"], "kind": position["kind"], "value": 0.0, "quantity": 0.0, "accounts": set()},
        )
        row["value"] += position["value"]
        row["quantity"] += position["quantity"] or 0.0
        row["accounts"].add(position["account_id"])

    holdings = []
    for row in sorted(merged.values(), key=lambda r: r["value"], reverse=True):
        holdings.append(
            {
                "symbol": row["symbol"],
                "kind": row["kind"],
                "value": round(row["value"], 2),
                "quantity": row["quantity"],
                "weight": row["value"] / total,
                "accounts": len(row["accounts"]),
            }
        )

    weights = [h["weight"] for h in holdings]
    top_one = weights[0]
    top_three = sum(weights[:3])
    herfindahl = sum(w * w for w in weights)
    effective = 1 / herfindahl

    kind_totals = defaultdict(float)
    for holding in holdings:
        kind_totals[holding["kind"]] += holding["weight"]
    by_kind = [
        {"kind": kind, "weight": weight}
        for kind, weight in sorted(kind_totals.items(), key=lambda item: item[1], reverse=True)
    ]

    flags = []
    points = 0

    if top_one >= TOP_HOLDING_HIGH:
        points += 35
        flags.append(
            {
                "id": "single_holding_high",
                "severity": "high",
                "title": "One holding dominates the portfolio",
                "detail": f"{holdings[0]['symbol']} is {percent(top_one)} of the portfolio. A drop in this one position moves the whole total.",
            }
        )
    elif top_one >= TOP_HOLDING_MEDIUM:
        points += 20
        flags.append(
            {
                "id": "single_holding_medium",
                "severity": "medium",
                "title": "Large single holding",
                "detail": f"{holdings[0]['symbol']} is {percent(top_one)} of the portfolio, above the {percent(TOP_HOLDING_MEDIUM)} guideline.",
            }
        )

    if len(holdings) > 3 and top_three > TOP_THREE_LIMIT:
        points += 15
        names = ", ".join(h["symbol"] for h in holdings[:3])
        flags.append(
            {
                "id": "top_three",
                "severity": "medium",
                "title": "Top three holdings carry most of the value",
                "detail": f"{names} together make up {percent(top_three)}.",
            }
        )

    if len(holdings) < MIN_HOLDINGS_LOW:
        points += 25
        flags.append(
            {
                "id": "few_holdings_high",
                "severity": "high",
                "title": "Very few holdings",
                "detail": f"Only {len(holdings)} distinct holding(s). Diversification is minimal.",
            }
        )
    elif len(holdings) < MIN_HOLDINGS_OK:
        points += 15
        flags.append(
            {
                "id": "few_holdings_medium",
                "severity": "medium",
                "title": "Few holdings",
                "detail": f"Only {len(holdings)} distinct holdings. Many guides suggest at least {MIN_HOLDINGS_OK}.",
            }
        )

    if by_kind and by_kind[0]["weight"] > KIND_LIMIT:
        points += 10
        flags.append(
            {
                "id": "asset_kind",
                "severity": "medium",
                "title": "Concentrated in one asset type",
                "detail": f"{percent(by_kind[0]['weight'])} of the portfolio is in '{by_kind[0]['kind']}'.",
            }
        )

    if unvalued:
        points += 5
        flags.append(
            {
                "id": "unvalued",
                "severity": "info",
                "title": "Some positions have no market value",
                "detail": f"{unvalued} position(s) were left out because SnapTrade returned no usable price or value.",
            }
        )

    if other_currencies:
        points += 5
        listed = ", ".join(f"{amount:,.2f} {code}" for code, amount in other_currencies.items())
        flags.append(
            {
                "id": "mixed_currency",
                "severity": "info",
                "title": "Holdings in more than one currency",
                "detail": f"Percentages use {currency} holdings only. Left out: {listed}. No exchange rates are applied.",
            }
        )

    if not flags:
        flags.append(
            {
                "id": "ok",
                "severity": "low",
                "title": "No concentration problems found",
                "detail": f"Largest holding is {percent(top_one)} and there are {len(holdings)} holdings.",
            }
        )

    points = min(points, 100)
    if points < 25:
        level = "low"
    elif points < 50:
        level = "medium"
    else:
        level = "high"

    return {
        "currency": currency,
        "total_value": round(total, 2),
        "holding_count": len(holdings),
        "top_holding_weight": top_one,
        "top_three_weight": top_three,
        "effective_holdings": round(effective, 2),
        "by_kind": by_kind,
        "holdings": holdings,
        "flags": flags,
        "risk_score": points,
        "level": level,
        "excluded": {"unvalued": unvalued, "other_currencies": other_currencies},
    }

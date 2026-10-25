from risk import analyze, normalize_position


def pos(symbol, value, kind="equity", currency="USD", account="a1"):
    return normalize_position(
        account,
        {"instrument": {"symbol": symbol, "kind": kind}, "market_value": value, "currency": currency},
    )


def ids(result):
    return {flag["id"] for flag in result["flags"]}


def test_normalize_reads_nested_fields():
    p = normalize_position(
        "acc",
        {"instrument": {"kind": "Equity", "symbol": "aapl"}, "quantity": "10", "price": 5, "currency": {"code": "usd"}},
    )
    assert p["symbol"] == "AAPL"
    assert p["kind"] == "equity"
    assert p["value"] == 50.0
    assert p["currency"] == "USD"


def test_normalize_handles_missing_data():
    p = normalize_position("acc", {})
    assert p["symbol"] == "UNKNOWN"
    assert p["value"] is None
    assert p["currency"] == "UNKNOWN"


def test_empty_portfolio():
    result = analyze([])
    assert result["level"] == "unknown"
    assert ids(result) == {"no_data"}


def test_single_holding_is_high_risk():
    result = analyze([pos("AAPL", 1000)])
    assert result["level"] == "high"
    assert {"single_holding_high", "few_holdings_high", "asset_kind"} <= ids(result)
    assert result["top_holding_weight"] == 1.0


def test_balanced_portfolio_is_low_risk():
    positions = [pos(s, 100, kind=k) for s, k in [("A", "equity"), ("B", "equity"), ("C", "bond"), ("D", "etf"), ("E", "equity"), ("F", "etf")]]
    result = analyze(positions)
    assert result["level"] == "low"
    assert ids(result) == {"ok"}
    assert result["holding_count"] == 6


def test_medium_concentration():
    positions = [pos("BIG", 300, kind="equity")] + [pos(s, 175, kind="etf") for s in "ABCD"]
    result = analyze(positions)
    assert "single_holding_medium" in ids(result)
    assert "single_holding_high" not in ids(result)


def test_top_three_flag():
    positions = [pos("A", 30), pos("B", 25), pos("C", 20), pos("D", 5, kind="etf"), pos("E", 5, kind="etf"), pos("F", 5, kind="bond")]
    result = analyze(positions)
    assert "top_three" in ids(result)


def test_same_symbol_merges_across_accounts():
    result = analyze([pos("AAPL", 100, account="a1"), pos("AAPL", 300, account="a2"), pos("MSFT", 100)])
    top = result["holdings"][0]
    assert top["symbol"] == "AAPL"
    assert top["value"] == 400.0
    assert top["accounts"] == 2
    assert result["holding_count"] == 2


def test_other_currency_is_reported_not_mixed():
    positions = [pos("A", 500), pos("B", 300), pos("C", 100, currency="CAD")]
    result = analyze(positions)
    assert result["currency"] == "USD"
    assert result["total_value"] == 800.0
    assert result["excluded"]["other_currencies"] == {"CAD": 100.0}
    assert "mixed_currency" in ids(result)


def test_unvalued_positions_are_counted():
    positions = [pos("A", 100), pos("B", None), pos("C", 0)]
    result = analyze(positions)
    assert result["excluded"]["unvalued"] == 2
    assert "unvalued" in ids(result)


def test_score_is_capped_and_weights_sum_to_one():
    result = analyze([pos("A", 10), pos("B", 5)])
    assert 0 <= result["risk_score"] <= 100
    assert abs(sum(h["weight"] for h in result["holdings"]) - 1) < 1e-9

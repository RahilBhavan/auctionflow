from auctionflow.config import TradingConfig
from auctionflow.risk import mes_stop_risk_cents, propose_risk, risk_budget_cents


def test_budget_is_minimum_of_two_hundred_and_ten_percent_buffer():
    assert risk_budget_cents(1_000_000) == 20_000
    assert risk_budget_cents(1_000) == 100


def test_mes_tick_cost_and_max_sizes():
    config = TradingConfig()
    assert mes_stop_risk_cents(100, 92, 1, config) == 1_000
    proposal = propose_risk(100, 92, 1_000_000, config=config)
    assert proposal.quantity == 2
    assert proposal.stop_risk_cents == 2_000


def test_cost_reserve_can_make_one_contract_unaffordable():
    config = TradingConfig(commission_cents_per_contract=600, slippage_cents_per_contract=500)
    proposal = propose_risk(100, 92, 1_000, config=config)
    assert proposal.quantity == 0
    assert proposal.stop_risk_cents == 0

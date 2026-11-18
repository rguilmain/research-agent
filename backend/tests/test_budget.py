"""Budget reservations, guard charging and concurrency policy, all offline."""

from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import SecretStr, ValidationError

from research_agent.budget import BudgetExhausted, BudgetLedger
from research_agent.catalog import (
    ceil_usd,
    conservative_model_cost,
    load_catalog,
    load_tool_prices,
    model_call_reservation,
)
from research_agent.config import Settings, production_readiness_errors
from research_agent.guards import ModelGuards, RunBudgetExhausted, RunContext, ToolGuards

LUNA = load_catalog()["gpt-6-luna"]
LIMIT = Decimal("5")


class FakeBudgetStore:
    """Records reservations and settlements, and can simulate a full day."""

    def __init__(self, refuse: bool = False) -> None:
        self.refuse = refuse
        self.reserved: list[tuple[str, Decimal]] = []
        self.settled: list[tuple[UUID, Decimal, str]] = []

    async def reserve_budget(self, run_id, kind, amount, limit, ttl) -> UUID:
        if self.refuse:
            raise BudgetExhausted("no room today")
        self.reserved.append((kind, amount))
        return uuid4()

    async def settle_budget(self, reservation_id, actual, status="settled") -> None:
        self.settled.append((reservation_id, actual, status))


def ledger(store: FakeBudgetStore) -> BudgetLedger:
    return BudgetLedger(store, uuid4(), LIMIT, timedelta(minutes=10))


def model_request(context, messages=None):
    return SimpleNamespace(
        runtime=SimpleNamespace(context=context),
        system_message=None,
        messages=messages or [HumanMessage(content="question")],
        tools=[],
    )


def tool_request(context, query="question", name="wikipedia"):
    return SimpleNamespace(
        runtime=SimpleNamespace(context=context),
        tool_call={"name": name, "args": {"query": query}, "id": "call"},
    )


def test_reservation_and_settlement_math():
    assert ceil_usd(Decimal("0.0000001")) == Decimal("0.000001")
    assert model_call_reservation(
        LUNA, input_tokens=1000, output_tokens=4096, attempts=2
    ) == Decimal("0.004296")
    # Cached tokens with no verified cached price are billed as ordinary input.
    assert conservative_model_cost(
        LUNA, input_tokens=1000, output_tokens=100, cached_input_tokens=400
    ) == Decimal("0.000150")
    with pytest.raises(ValueError):
        conservative_model_cost(LUNA, input_tokens=100, output_tokens=10, cached_input_tokens=200)
    with pytest.raises(ValueError):
        model_call_reservation(LUNA, input_tokens=1, output_tokens=1, attempts=0)


def test_tool_prices_are_declared_and_wolfram_is_unpriced():
    prices = load_tool_prices()
    assert prices["wikipedia"] == Decimal("0")
    assert prices["tavily_search"] == Decimal("0.008")
    assert "wolfram_alpha" not in prices


def test_readiness_reports_wolfram_without_a_price():
    errors = production_readiness_errors(
        Settings(app_env="test", wolfram_alpha_appid=SecretStr("id"))
    )
    assert "WOLFRAM_ALPHA_APPID is configured without a price in models.toml" in errors
    plain = production_readiness_errors(Settings(app_env="test"))
    assert not any("WOLFRAM" in item for item in plain)


@pytest.mark.parametrize(
    "overrides",
    [
        {"global_daily_budget_usd": Decimal("0")},
        {"global_daily_budget_usd": Decimal("-1")},
        # A hold must outlive the run that owns it, or expiry would double-charge the day.
        {"budget_reservation_ttl_minutes": 2, "run_timeout_s": 120},
        {"max_active_runs": 1, "max_active_runs_per_principal": 2},
        {"max_active_runs": 0},
    ],
)
def test_budget_and_concurrency_settings_must_be_consistent(overrides):
    with pytest.raises(ValidationError):
        Settings(app_env="test", **overrides)


async def test_model_call_reserves_a_hold_then_settles_the_actual_cost():
    store = FakeBudgetStore()
    context = RunContext(budget=ledger(store))

    async def handler(_request):
        return SimpleNamespace(
            result=[
                AIMessage(
                    content="answer",
                    usage_metadata={
                        "input_tokens": 1000,
                        "output_tokens": 100,
                        "total_tokens": 1100,
                        "input_token_details": {"cache_read": 400},
                        "output_token_details": {},
                    },
                )
            ]
        )

    await ModelGuards(Settings(), LUNA, attempts=2).awrap_model_call(
        model_request(context), handler
    )
    kind, hold = store.reserved[0]
    assert kind == "model"
    _, actual, status = store.settled[0]
    assert status == "settled"
    assert actual == conservative_model_cost(
        LUNA, input_tokens=1000, output_tokens=100, cached_input_tokens=400
    )
    # The hold is the worst case, so it always covers what was actually settled.
    assert hold >= actual > 0


async def test_a_full_day_refuses_the_call_before_dispatch():
    store = FakeBudgetStore(refuse=True)
    context = RunContext(budget=ledger(store))
    dispatched = False

    async def handler(_request):
        nonlocal dispatched
        dispatched = True
        return SimpleNamespace(result=[])

    with pytest.raises(RunBudgetExhausted):
        await ModelGuards(Settings(), LUNA).awrap_model_call(model_request(context), handler)
    assert not dispatched
    assert context.model_calls == 1 and store.settled == []


async def test_missing_usage_settles_the_full_hold():
    store = FakeBudgetStore()
    context = RunContext(budget=ledger(store))

    async def handler(_request):
        return SimpleNamespace(result=[AIMessage(content="answer")])

    await ModelGuards(Settings(), LUNA).awrap_model_call(model_request(context), handler)
    _, hold = store.reserved[0]
    assert store.settled[0][1] == hold


async def test_a_failed_dispatch_still_charges_the_hold():
    store = FakeBudgetStore()
    context = RunContext(budget=ledger(store))

    async def handler(_request):
        raise TimeoutError("provider timed out")

    with pytest.raises(TimeoutError):
        await ModelGuards(Settings(), LUNA).awrap_model_call(model_request(context), handler)
    _, hold = store.reserved[0]
    assert store.settled[0][1] == hold


async def test_no_ledger_means_nothing_is_reserved():
    store = FakeBudgetStore()

    async def handler(_request):
        return SimpleNamespace(result=[AIMessage(content="answer")])

    await ModelGuards(Settings(), LUNA).awrap_model_call(model_request(RunContext()), handler)
    assert store.reserved == [] and store.settled == []


async def test_unpriced_tool_is_refused_without_executing():
    executed = False

    async def handler(_request):
        nonlocal executed
        executed = True
        return ToolMessage(content="ok", tool_call_id="call")

    guard = ToolGuards(Settings(), {"wikipedia": Decimal("0")})
    result = await guard.awrap_tool_call(tool_request(RunContext(), name="tavily_search"), handler)
    assert "unavailable" in result.content and not executed


async def test_free_tool_never_touches_the_ledger():
    store = FakeBudgetStore()
    context = RunContext(budget=ledger(store))

    async def handler(_request):
        return ToolMessage(content="ok", tool_call_id="call")

    await ToolGuards(Settings(), {"wikipedia": Decimal("0")}).awrap_tool_call(
        tool_request(context), handler
    )
    assert store.reserved == [] and store.settled == []


async def test_paid_tool_is_charged_in_full_even_when_it_fails():
    store = FakeBudgetStore()
    context = RunContext(budget=ledger(store))

    async def handler(_request):
        raise RuntimeError("search backend down")

    guard = ToolGuards(Settings(), {"tavily_search": Decimal("0.008")})
    with pytest.raises(RuntimeError):
        await guard.awrap_tool_call(tool_request(context, name="tavily_search"), handler)
    assert store.reserved == [("tool", Decimal("0.008"))]
    assert store.settled[0][1] == Decimal("0.008")


async def test_paid_tool_is_not_executed_when_the_budget_is_full():
    store = FakeBudgetStore(refuse=True)
    context = RunContext(budget=ledger(store))
    executed = False

    async def handler(_request):
        nonlocal executed
        executed = True
        return ToolMessage(content="ok", tool_call_id="call")

    guard = ToolGuards(Settings(), {"tavily_search": Decimal("0.008")})
    result = await guard.awrap_tool_call(tool_request(context, name="tavily_search"), handler)
    assert "budget exhausted" in result.content and not executed
    assert context.tool_calls == {}


async def test_ledger_skips_free_work_and_missing_reservations():
    store = FakeBudgetStore()
    book = ledger(store)
    assert await book.reserve("tool", Decimal("0")) is None
    await book.settle(None, Decimal("1"))
    assert store.reserved == [] and store.settled == []

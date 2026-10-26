"""Stage 5 routing tests (RTE-1, RTE-3, RTE-4)."""

from __future__ import annotations

from datetime import date

import pytest

from catalog import MetricRegistry
from contracts.models import GuardResult
from router import Router
from schema_retriever.catalog import CatalogColumn, CatalogTable, SchemaCatalog


class NoCallClient:
    def chat_json(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("this route must not call the model")


class FakeClient:
    def __init__(self, payload, tokens_in=20, tokens_out=10):
        self.payload = payload
        self.tokens_in = tokens_in
        self.tokens_out = tokens_out
        self.calls = 0

    def chat_json(self, *args, **kwargs):  # noqa: ANN002, ANN003
        self.calls += 1
        return self.payload, self.tokens_in, self.tokens_out


def _catalog() -> SchemaCatalog:
    return SchemaCatalog(
        version="router-test",
        tables=(
            CatalogTable(
                name="orders",
                columns=(
                    CatalogColumn(name="order_id", data_type="TEXT"),
                    CatalogColumn(
                        name="customer_id",
                        data_type="TEXT",
                    ),
                    CatalogColumn(
                        name="order_status",
                        data_type="TEXT",
                        values=("delivered", "shipped", "canceled"),
                    ),
                    CatalogColumn(
                        name="order_purchase_timestamp",
                        data_type="TEXT",
                    ),
                ),
            ),
            CatalogTable(
                name="customers",
                columns=(
                    CatalogColumn(name="customer_id", data_type="TEXT"),
                    CatalogColumn(
                        name="customer_state",
                        data_type="TEXT",
                        values=("SP", "RJ", "MG"),
                    ),
                ),
            ),
            CatalogTable(
                name="order_payments",
                columns=(
                    CatalogColumn(name="order_id", data_type="TEXT"),
                    CatalogColumn(name="payment_value", data_type="REAL"),
                ),
            ),
            CatalogTable(
                name="order_reviews",
                columns=(
                    CatalogColumn(name="review_id", data_type="TEXT"),
                    CatalogColumn(name="review_score", data_type="INTEGER"),
                ),
            ),
        ),
    )


def _router(client=None, **kwargs) -> Router:
    return Router(
        catalog=_catalog(),
        registry=MetricRegistry.default(),
        client=client or NoCallClient(),
        reference_date=date(2026, 10, 25),
        **kwargs,
    )


def test_explicit_diagnostic_question_routes_to_anomaly_and_driver_without_model():
    client = NoCallClient()
    result = _router(client).route("Why did orders drop in August 2018?")

    assert result.route_labels[0] == "anomaly"
    assert "driver" in result.route_labels
    assert result.intent_label == "Diagnostic"
    assert result.confidence >= 0.70
    assert result.slots.metric_terms == ("order_count",)
    assert result.slots.time.current == _period("2018-08-01", "2018-09-01")
    assert result.slots.time.grain == "month"
    assert result.reason


def test_lookup_slots_are_typed_and_expose_route_labels():
    result = _router().route(
        "What was revenue by customer state in August 2018?"
    )

    assert result.primary_route == "lookup"
    assert result.route_labels == ("lookup",)
    assert result.intent_label == "Descriptive"
    assert result.slots.dimensions == ("customer_state",)
    assert result.slots.time.current == _period("2018-08-01", "2018-09-01")
    assert result.slots.time.grain == "month"
    assert result.signals.classifier.prob > 0.0
    assert result.signals.rules.matched == ()


def test_vague_question_returns_structured_clarification_below_threshold():
    result = _router().route("How are sales doing?")

    assert result.primary_route == "clarify"
    assert result.route_labels == ("clarify",)
    assert result.confidence < 0.70
    assert result.clarification is not None
    assert "sales" in result.clarification.what_understood.lower()
    needed = result.clarification.what_needs_clarification.lower()
    assert "period" in needed or "time" in needed


def test_risk_question_uses_catalog_keywords():
    result = _router().route("Which customers are likely to churn?")

    assert result.primary_route == "risk"
    assert result.intent_label == "Prescriptive"
    assert result.confidence >= 0.70


def test_out_of_scope_nps_is_refused_before_any_model_call():
    result = _router(NoCallClient()).route("What is NPS?")

    assert result.primary_route == "refused"
    assert result.route_labels == ("refused",)
    assert result.reason


def test_blocked_guard_result_wins_over_routing():
    guard = GuardResult(
        passed=False,
        reason="injection",
        matched_rule="ignore_previous",
    )
    result = _router().route("Why did orders drop in August 2018?", guard_result=guard)

    assert result.primary_route == "blocked"
    assert result.route_labels == ("blocked",)
    assert result.confidence == 1.0


def test_ambiguous_question_uses_validated_model_signal_as_tie_breaker():
    payload = {
        "route": "risk",
        "intent_label": "Prescriptive",
        "metric_terms": ["churn"],
        "dimensions": ["customer_state"],
        "time_expression": None,
        "action_intent": False,
        "confidence": 0.88,
        "reasoning": "The question asks about future customer loss.",
    }
    client = FakeClient(payload)
    result = _router(client).route("Which accounts should we prioritize?")

    assert client.calls == 1
    assert result.primary_route == "risk"
    assert result.confidence >= 0.70
    assert result.signals.model is not None
    assert result.signals.model.proposed_label == "risk"
    assert result.signals.model.usage is not None
    assert result.signals.model.usage.tokens_in == 20
    assert result.signals.model.usage.tokens_out == 10


def test_invalid_model_route_fails_closed():
    payload = {
        "route": "write_sql",
        "intent_label": "Diagnostic",
        "metric_terms": [],
        "dimensions": [],
        "time_expression": None,
        "action_intent": False,
        "confidence": 0.99,
        "reasoning": "bad route",
    }
    result = _router(FakeClient(payload)).route("Which accounts should we prioritize?")

    assert result.primary_route == "clarify"
    assert result.confidence < 0.70
    assert result.clarification is not None


def test_period_parser_handles_quarters_and_years():
    quarter = _router().route("What drove revenue in Q1 2018?")
    assert quarter.slots.time.current == _period("2018-01-01", "2018-04-01")
    assert quarter.slots.time.grain == "quarter"
    assert quarter.primary_route == "driver"

    year = _router().route("What was GMV in 2017?")
    assert year.slots.time.current == _period("2017-01-01", "2018-01-01")
    assert year.slots.time.grain == "year"


def test_comparative_question_extracts_two_periods_and_comparative_intent():
    router = _router()
    route = router.route("difference in total sales in 2018 compared to 2017")
    assert route.primary_route == "lookup"
    assert route.intent_label == "Comparative"
    assert route.slots.time.current == _period("2018-01-01", "2019-01-01")
    assert route.slots.time.comparison == _period("2017-01-01", "2018-01-01")
    assert route.slots.metric_terms == ("revenue",)


def _period(start: str, end: str):
    from contracts.models import Period

    return Period(start=date.fromisoformat(start), end=date.fromisoformat(end))

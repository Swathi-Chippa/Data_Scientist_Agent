"""Tests for Ollama-backed metric intent and slot resolution (MET-2, MET-4)."""

from __future__ import annotations

import json

from catalog import MetricRegistry
from catalog.metric_resolver import MetricResolver
from contracts.models import ModelUsage
from models.client import ModelUnavailableError, OllamaClient


class FakeReasoningClient:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls = 0
        self.messages = None

    def chat_json(self, messages, **kwargs):  # noqa: ANN001, ANN003
        self.calls += 1
        self.messages = messages
        return self.payload, ModelUsage(
            name="deepseek-r1:7b",
            version="deepseek-r1:7b",
            tokens_in=300,
            tokens_out=80,
        )


class FailingReasoningClient:
    def chat_json(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise ModelUnavailableError("Ollama is not running")


def test_maps_complex_phrasing_with_mocked_ollama_response():
    payload = {
        "metric_id": "revenue",
        "dimensions": ["customer_state"],
        "time_grain": "quarter",
        "period": {"start": "2018-01-01", "end": "2018-04-01"},
        "filters": [
            {
                "table": "orders",
                "column": "order_status",
                "operator": "=",
                "value": "delivered",
            }
        ],
        "reasoning": "Revenue is the governed payment total; completed maps to delivered.",
    }
    client = FakeReasoningClient(payload)
    resolver = MetricResolver(MetricRegistry.default(), client=client)

    resolution = resolver.resolve(
        "How much did we make from completed orders in the first quarter of 2018 by state?"
    )

    assert client.calls == 1
    assert resolution.coverage == "full"
    assert resolution.metric_id == "revenue"
    assert resolution.definition is not None
    assert resolution.time_grain == "quarter"
    assert resolution.period is not None
    assert resolution.period.start.isoformat() == "2018-01-01"
    assert resolution.period.end.isoformat() == "2018-04-01"
    assert resolution.resolved_dimensions[0].requested == "customer_state"
    assert resolution.resolved_dimensions[0].allowed is True
    assert resolution.filters[0].column == "order_status"
    assert resolution.filters[0].value == "delivered"
    assert resolution.model is not None
    assert resolution.model.name == "deepseek-r1:7b"
    assert resolution.reasoning is not None
    assert "completed" in resolution.reasoning
    assert "revenue" in json.dumps(client.messages).lower()


def test_simple_governed_question_never_calls_the_model():
    client = FakeReasoningClient(
        {
            "metric_id": "revenue",
            "dimensions": [],
            "reasoning": "should not be called",
        }
    )
    resolver = MetricResolver(MetricRegistry.default(), client=client)

    resolution = resolver.resolve("What was GMV by state in 2018?")

    assert client.calls == 0
    assert resolution.coverage == "full"
    assert resolution.metric_id == "gmv"
    assert resolution.period is not None
    assert resolution.period.start.isoformat() == "2018-01-01"
    assert resolution.period.end.isoformat() == "2019-01-01"
    assert resolution.resolved_dimensions[0].requested == "customer_state"
    assert resolution.resolved_dimensions[0].allowed is True


def test_model_unavailable_falls_back_to_ungoverned_caveat():
    resolver = MetricResolver(
        MetricRegistry.default(),
        client=FailingReasoningClient(),
    )

    resolution = resolver.resolve("How much did we make last quarter?")

    assert resolution.coverage == "none"
    assert resolution.metric_id is None
    assert "ungoverned metric" in resolution.caveats
    assert any("unavailable" in caveat.lower() for caveat in resolution.caveats)


def test_model_maps_only_to_registered_metrics():
    client = FakeReasoningClient(
        {
            "metric_id": "profit_margin",
            "dimensions": [],
            "reasoning": "unsupported metric",
        }
    )
    resolver = MetricResolver(MetricRegistry.default(), client=client)

    resolution = resolver.resolve("How much did we make last quarter?")

    assert resolution.coverage == "none"
    assert resolution.metric_id is None
    assert "ungoverned metric" in resolution.caveats
    assert any("profit_margin" in caveat for caveat in resolution.caveats)


def test_ollama_client_uses_openai_compatible_chat_endpoint():
    captured: dict[str, object] = {}

    def transport(url, headers, body, timeout):  # noqa: ANN001
        captured["url"] = url
        captured["headers"] = headers
        captured["payload"] = json.loads(body)
        captured["timeout"] = timeout
        return json.dumps(
            {
                "model": "deepseek-r1:7b",
                "choices": [
                    {
                        "message": {
                            "content": "```json\n"
                            '{"metric_id": "gmv", "dimensions": []}'
                            "\n```",
                            "reasoning_content": "GMV is a governed metric.",
                        }
                    }
                ],
                "usage": {
                    "prompt_tokens": 123,
                    "completion_tokens": 45,
                    "total_tokens": 168,
                },
            }
        ).encode("utf-8")

    client = OllamaClient(transport=transport)
    payload, usage = client.chat_json(
        [{"role": "user", "content": "What was GMV?"}]
    )

    assert payload == {"metric_id": "gmv", "dimensions": []}
    assert usage.name == "deepseek-r1:7b"
    assert usage.tokens_in == 123
    assert usage.tokens_out == 45
    assert captured["url"] == "http://localhost:11434/v1/chat/completions"
    assert captured["payload"]["model"] == "deepseek-r1:7b"
    assert captured["payload"]["stream"] is False
    assert captured["payload"]["messages"][0]["content"] == "What was GMV?"
    assert captured["timeout"] == 60.0

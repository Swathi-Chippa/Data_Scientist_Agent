"""Three-signal intent routing for Stage 5 (RTE-1, RTE-3, RTE-4).

Rules, a catalog/keyword classifier and an optional local model contribute to a
single typed route. Rules win when they are explicit, the model is only
consulted when the deterministic signals cannot name a metric, and any result
below the confidence threshold is converted into a structured clarification.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from catalog import MetricRegistry
from contracts.models import (
    ClarificationResult,
    ClassifierSignal,
    GuardResult,
    IntentLabel,
    MetricDefinition,
    MetricTimeGrain,
    ModelRouteSignal,
    ModelUsage,
    Period,
    RouteLabel,
    RouteSlots,
    RouterResult,
    RouterSignals,
    RulesSignal,
    SlotFilter,
    TimeSlot,
)
from schema_retriever.catalog import SchemaCatalog

_ROUTES = frozenset(
    {"lookup", "anomaly", "driver", "risk", "clarify", "refused", "blocked"}
)
_INTENTS = frozenset(
    {"Descriptive", "Comparative", "Diagnostic", "Prescriptive"}
)

_MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}
_MONTH_PATTERN = re.compile(
    r"\b(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\s+(20\d{2})\b",
    re.IGNORECASE,
)
_QUARTER_PATTERN = re.compile(
    r"\bq([1-4])(?:\s*(?:of\s*)?(20\d{2}))?\b",
    re.IGNORECASE,
)
_QUARTER_WORD_PATTERN = re.compile(
    r"\b(first|second|third|fourth)\s+quarter(?:\s+(?:of\s+)?(20\d{2}))?\b",
    re.IGNORECASE,
)
_YEAR_PATTERN = re.compile(r"\b(20\d{2})\b")
_GRAIN_PHRASES = (
    (r"\bby day\b|\bdaily\b", "day"),
    (r"\bby week\b|\bweekly\b", "week"),
    (r"\bby month\b|\bmonthly\b", "month"),
    (r"\bby quarter\b|\bquarterly\b", "quarter"),
    (r"\bby year\b|\byearly\b|\bannual\b", "year"),
)

_OUT_OF_SCOPE = {
    "nps": "net promoter score",
    "net_promoter": "net promoter score",
    "profit": "profit",
    "margin": "profit margin",
    "cogs": "cost of goods sold",
    "inventory": "inventory",
    "stock_level": "inventory",
    "employee": "employee data",
    "salary": "salary data",
    "marketing_spend": "marketing spend",
    "ad_spend": "advertising spend",
    "website_traffic": "website traffic",
}
_OUT_OF_SCOPE_PHRASES = (
    ("net promoter score", "net_promoter"),
    ("net promoter", "net_promoter"),
    ("nps", "nps"),
    ("profit margin", "margin"),
    ("gross margin", "margin"),
    ("profit", "profit"),
    ("cost of goods", "cogs"),
    ("cogs", "cogs"),
    ("inventory", "inventory"),
    ("stock level", "stock_level"),
    ("employee", "employee"),
    ("salary", "salary"),
    ("marketing spend", "marketing_spend"),
    ("ad spend", "ad_spend"),
    ("advertising spend", "ad_spend"),
    ("website traffic", "website_traffic"),
)
_RISK_PHRASES = (
    "churn",
    "retention",
    "at risk",
    "likely to leave",
    "will leave",
    "might leave",
    "cancel",
    "cancellation",
    "fraud",
    "default",
)
_DIAGNOSTIC_PHRASES = (
    "why did",
    "why has",
    "why have",
    "why is",
    "why are",
    "what caused",
    "reason for",
    "explain",
)
_DRIVER_PHRASES = (
    "what drove",
    "what is driving",
    "what's driving",
    "driver of",
    "drivers of",
    "main driver",
    "contributed to",
    "contribution of",
    "attribut",
)
_CHANGE_WORDS = (
    "drop",
    "dropped",
    "decline",
    "declined",
    "fall",
    "fell",
    "decrease",
    "decreased",
    "spike",
    "spiked",
    "jump",
    "jumped",
    "increase",
    "increased",
    "unusual",
    "anomaly",
    "outlier",
    "shift",
    "changed",
)
_VAGUE_WORDS = (
    "doing",
    "performing",
    "trending",
    "lately",
    "these days",
    "recently",
    "overall",
)
_LOOKUP_CUES = (
    "what was",
    "what is",
    "what were",
    "how many",
    "how much",
    "show",
    "list",
    "give me",
    "average",
    "total",
    "compare",
    "compared",
    "difference",
    "versus",
    " vs ",
    "trend",
    "by ",
    "broken down",
)
_COMPARATIVE_CUES = (
    "compare",
    "compared",
    "comparing",
    "comparison",
    "difference",
    "differences",
    "different",
    "versus",
    " vs ",
    "growth",
    "change",
    "year over year",
    "month over month",
    "trend",
)
_DOMAIN_TERMS = (
    ("orders", "order"),
    ("customers", "customer"),
    ("reviews", "review"),
    ("payments", "payment"),
    ("products", "product"),
    ("sellers", "seller"),
)
_DIMENSION_SYNONYMS = {
    "customer_state": ("state", "states", "customer state", "customer states"),
    "customer_city": ("city", "cities", "customer city", "customer cities"),
    "order_status": ("status", "order status", "order statuses"),
    "payment_type": ("payment type", "payment method", "payment types"),
    "product_category_name": (
        "category",
        "categories",
        "product category",
        "product categories",
    ),
}
_STATUS_WORDS = {
    "delivered": "delivered",
    "completed": "delivered",
    "shipped": "shipped",
    "canceled": "canceled",
    "cancelled": "canceled",
    "processing": "processing",
    "invoiced": "invoiced",
    "approved": "approved",
    "unavailable": "unavailable",
}
_FALLBACK_METRICS = (
    (
        "aov",
        r"\b(aov|average order value|average basket|average order size|average order amount)\b",
    ),
    (
        "gmv",
        r"\b(gmv|gross merchandise value|gross merchandise volume)\b",
    ),
    (
        "revenue",
        r"\b(revenue|sales|turnover|net sales)\b",
    ),
    (
        "order_count",
        r"\b(order count|order volume|number of orders|orders?)\b",
    ),
)

_SYSTEM_PROMPT = """You are an intent router for an e-commerce data agent.
Return one JSON object with these keys:
route: one of lookup, anomaly, driver, risk, clarify, refused
intent_label: one of Descriptive, Comparative, Diagnostic, Prescriptive
metric_terms: array of metric words from the question
dimensions: array of grouping dimensions from the question
time_expression: the time phrase or null
action_intent: boolean
confidence: number between 0 and 1
reasoning: one short sentence
Use risk for churn/retention/fraud risk. Use diagnose-style labels only when
the question asks for a cause or anomaly. Never invent a governed metric."""


@dataclass(frozen=True, slots=True)
class _ModelDecision:
    label: RouteLabel
    intent: IntentLabel
    slots: RouteSlots
    confidence: float
    reasoning: str | None


def _normalize(text: str) -> str:
    lowered = text.lower().replace("&", " and ")
    lowered = re.sub(r"[_\-/]+", " ", lowered)
    lowered = re.sub(r"[^\w\s]", " ", lowered)
    return " ".join(lowered.split())


def _contains(text: str, phrase: str) -> bool:
    return (
        re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text) is not None
    )


def _add_months(month_start: date, months: int) -> date:
    index = month_start.year * 12 + (month_start.month - 1) + months
    return date(index // 12, index % 12 + 1, 1)


def _previous_period(period: Period) -> Period:
    length = period.end - period.start
    return Period(start=period.start - length, end=period.start)


def _month_period(year: int, month: int) -> Period:
    start = date(year, month, 1)
    return Period(start=start, end=_add_months(start, 1))


def _quarter_period(year: int, quarter: int) -> Period:
    start = date(year, (quarter - 1) * 3 + 1, 1)
    return Period(start=start, end=_add_months(start, 3))


def _year_period(year: int) -> Period:
    return Period(start=date(year, 1, 1), end=date(year + 1, 1, 1))


def _grain_phrase(question: str) -> MetricTimeGrain | None:
    for pattern, grain in _GRAIN_PHRASES:
        if re.search(pattern, question):
            return grain  # type: ignore[return-value]
    return None


def _time_slot(question: str, reference_date: date) -> TimeSlot:
    normalized = _normalize(question)
    grain = _grain_phrase(normalized)

    month_matches = list(_MONTH_PATTERN.finditer(question))
    if len(month_matches) >= 2:
        m1 = _MONTHS[month_matches[0].group(1).lower()]
        y1 = int(month_matches[0].group(2))
        m2 = _MONTHS[month_matches[1].group(1).lower()]
        y2 = int(month_matches[1].group(2))
        current = _month_period(y1, m1)
        comparison = _month_period(y2, m2)
        return TimeSlot(
            raw=f"{month_matches[0].group(0)} vs {month_matches[1].group(0)}",
            current=current,
            comparison=comparison,
            grain="month",
        )
    if len(month_matches) == 1:
        month = _MONTHS[month_matches[0].group(1).lower()]
        year = int(month_matches[0].group(2))
        current = _month_period(year, month)
        return TimeSlot(
            raw=month_matches[0].group(0),
            current=current,
            comparison=_previous_period(current),
            grain="month",
        )

    quarter_matches = list(_QUARTER_PATTERN.finditer(question))
    if len(quarter_matches) >= 2:
        q1 = int(quarter_matches[0].group(1))
        y1 = int(quarter_matches[0].group(2)) if quarter_matches[0].group(2) else reference_date.year
        q2 = int(quarter_matches[1].group(1))
        y2 = int(quarter_matches[1].group(2)) if quarter_matches[1].group(2) else reference_date.year
        current = _quarter_period(y1, q1)
        comparison = _quarter_period(y2, q2)
        return TimeSlot(
            raw=f"{quarter_matches[0].group(0)} vs {quarter_matches[1].group(0)}",
            current=current,
            comparison=comparison,
            grain="quarter",
        )
    if len(quarter_matches) == 1:
        quarter = int(quarter_matches[0].group(1))
        year = (
            int(quarter_matches[0].group(2))
            if quarter_matches[0].group(2)
            else reference_date.year
        )
        current = _quarter_period(year, quarter)
        return TimeSlot(
            raw=quarter_matches[0].group(0),
            current=current,
            comparison=_previous_period(current),
            grain="quarter",
        )

    quarter_word = _QUARTER_WORD_PATTERN.search(question)
    if quarter_word is not None:
        quarter = (
            "first",
            "second",
            "third",
            "fourth",
        ).index(quarter_word.group(1).lower()) + 1
        year = (
            int(quarter_word.group(2))
            if quarter_word.group(2)
            else reference_date.year
        )
        current = _quarter_period(year, quarter)
        return TimeSlot(
            raw=quarter_word.group(0),
            current=current,
            comparison=_previous_period(current),
            grain="quarter",
        )

    years = _YEAR_PATTERN.findall(question)
    if len(years) >= 2:
        current = _year_period(int(years[0]))
        comparison = _year_period(int(years[1]))
        return TimeSlot(
            raw=f"{years[0]} vs {years[1]}",
            current=current,
            comparison=comparison,
            grain=grain or "year",
        )
    if len(years) == 1:
        current = _year_period(int(years[0]))
        return TimeSlot(
            raw=years[0],
            current=current,
            comparison=_previous_period(current),
            grain=grain or "year",
        )

    current_month = date(reference_date.year, reference_date.month, 1)
    if "last month" in normalized:
        current = _month_period(
            _add_months(current_month, -1).year,
            _add_months(current_month, -1).month,
        )
        return TimeSlot(
            raw="last month",
            current=current,
            comparison=_previous_period(current),
            grain="month",
        )
    if "this month" in normalized:
        current = _month_period(current_month.year, current_month.month)
        return TimeSlot(
            raw="this month",
            current=current,
            comparison=_previous_period(current),
            grain="month",
        )
    if "last quarter" in normalized:
        current = _quarter_period(
            _add_months(current_month, -3).year,
            (_add_months(current_month, -3).month - 1) // 3 + 1,
        )
        return TimeSlot(
            raw="last quarter",
            current=current,
            comparison=_previous_period(current),
            grain="quarter",
        )
    if "this quarter" in normalized:
        current = _quarter_period(
            current_month.year, (current_month.month - 1) // 3 + 1
        )
        return TimeSlot(
            raw="this quarter",
            current=current,
            comparison=_previous_period(current),
            grain="quarter",
        )
    if "last year" in normalized:
        current = _year_period(reference_date.year - 1)
        return TimeSlot(
            raw="last year",
            current=current,
            comparison=_previous_period(current),
            grain="year",
        )
    if "this year" in normalized:
        current = _year_period(reference_date.year)
        return TimeSlot(
            raw="this year",
            current=current,
            comparison=_previous_period(current),
            grain="year",
        )
    return TimeSlot(grain=grain)


def _classify_metric(
    question: str, registry: MetricRegistry
) -> tuple[MetricDefinition | None, str | None]:
    normalized = _normalize(question)
    match = registry.match_question(normalized)
    if match is not None:
        return match[0], match[1]
    for metric_id, pattern in _FALLBACK_METRICS:
        if re.search(pattern, normalized):
            return registry.get(metric_id), pattern
    return None, None


def _metric_dimensions(
    question: str,
    metric: MetricDefinition | None,
    catalog: SchemaCatalog,
) -> tuple[str, ...]:
    normalized = _normalize(question)
    found: list[str] = []
    if metric is not None:
        for dimension in metric.allowed_dimensions:
            if dimension.time_grain is not None:
                continue
            phrases = (dimension.name, *dimension.synonyms)
            if any(
                _contains(normalized, _normalize(phrase))
                for phrase in phrases
            ):
                found.append(dimension.name)
    available = {
        column.name.lower()
        for table in catalog.tables
        for column in table.columns
    }
    for name, synonyms in _DIMENSION_SYNONYMS.items():
        if name not in available:
            continue
        if name in found:
            continue
        if any(_contains(normalized, _normalize(item)) for item in synonyms):
            found.append(name)
    return tuple(dict.fromkeys(found))


def _filters(question: str) -> tuple[SlotFilter, ...]:
    normalized = _normalize(question)
    return tuple(
        SlotFilter(term=word, value_phrase=value)
        for word, value in _STATUS_WORDS.items()
        if _contains(normalized, word)
    )


def _domain_hits(question: str, catalog: SchemaCatalog) -> tuple[str, ...]:
    normalized = _normalize(question)
    hits = {
        table
        for table, phrase in _DOMAIN_TERMS
        if _contains(normalized, phrase)
        and catalog.get_table(table) is not None
    }
    return tuple(sorted(hits))


def _rule_signal(question: str) -> RulesSignal:
    normalized = _normalize(question)
    matched: list[str] = []

    for phrase, code in _OUT_OF_SCOPE_PHRASES:
        if _contains(normalized, _normalize(phrase)):
            matched.append(code)
            return RulesSignal(matched=tuple(matched), label="refused")

    for phrase in _RISK_PHRASES:
        if _contains(normalized, _normalize(phrase)):
            matched.append(_normalize(phrase).replace(" ", "_"))
    if matched:
        return RulesSignal(matched=tuple(matched), label="risk")

    driver_hit = any(
        _contains(normalized, _normalize(phrase))
        for phrase in _DRIVER_PHRASES
    )
    if driver_hit:
        matched.append("driver_phrase")
        return RulesSignal(matched=tuple(matched), label="driver")

    diagnostic_hit = any(
        _contains(normalized, _normalize(phrase))
        for phrase in _DIAGNOSTIC_PHRASES
    )
    change_hit = any(
        _contains(normalized, _normalize(word)) for word in _CHANGE_WORDS
    )
    if diagnostic_hit:
        matched.append("diagnostic_phrase")
    if change_hit:
        matched.append("change_word")
    if diagnostic_hit or change_hit:
        return RulesSignal(matched=tuple(matched), label="anomaly")
    return RulesSignal()


def _classifier_signal(
    question: str,
    *,
    metric: MetricDefinition | None,
    dimensions: tuple[str, ...],
    time: TimeSlot,
    domains: tuple[str, ...],
) -> ClassifierSignal:
    normalized = _normalize(question)
    score = 0.20
    if metric is not None:
        score += 0.30
    if time.current is not None:
        score += 0.20
    if dimensions:
        score += 0.08
    if any(_contains(normalized, cue.strip()) for cue in _LOOKUP_CUES):
        score += 0.20
    if domains:
        score += 0.30
    if any(_contains(normalized, word) for word in _VAGUE_WORDS):
        score -= 0.25
    score = min(max(score, 0.0), 0.98)
    label: RouteLabel | None = (
        "lookup" if metric is not None or domains else None
    )
    return ClassifierSignal(
        label=label,
        prob=score,
        metric_terms=(metric.metric_id,) if metric is not None else (),
        dimensions=dimensions,
    )


def _intent_for_lookup(question: str) -> IntentLabel:
    normalized = _normalize(question)
    if any(_contains(normalized, cue.strip()) for cue in _COMPARATIVE_CUES):
        return "Comparative"
    return "Descriptive"


class Router:
    """Fuses deterministic rules, catalog keyword classification and a model."""

    def __init__(
        self,
        *,
        catalog: SchemaCatalog,
        registry: MetricRegistry | None = None,
        client: object | None = None,
        threshold: float = 0.70,
        reference_date: date | None = None,
    ) -> None:
        if not 0.0 < threshold <= 1.0:
            raise ValueError("threshold must be between 0 and 1")
        self._catalog = catalog
        self._registry = registry or MetricRegistry.default()
        self._client = client
        self._threshold = threshold
        self._reference_date = reference_date or date.today()

    @property
    def threshold(self) -> float:
        return self._threshold

    @property
    def registry(self) -> MetricRegistry:
        return self._registry

    def route(
        self,
        question: str,
        *,
        guard_result: GuardResult | None = None,
    ) -> RouterResult:
        if not question or not question.strip():
            raise ValueError("question must not be empty")
        if guard_result is not None and not guard_result.passed:
            return self._blocked(question, guard_result)

        normalized = _normalize(question)
        metric, metric_phrase = _classify_metric(question, self._registry)
        time = _time_slot(question, self._reference_date)
        dimensions = _metric_dimensions(question, metric, self._catalog)
        domains = _domain_hits(question, self._catalog)
        filters = _filters(question)
        slots = RouteSlots(
            metric_terms=(metric.metric_id,) if metric is not None else (),
            dimensions=dimensions,
            filters=filters,
            time=time,
            action_intent=any(
                _contains(normalized, word)
                for word in ("recommend", "should", "action", "mitigate")
            ),
        )
        rules = _rule_signal(question)
        classifier = _classifier_signal(
            question,
            metric=metric,
            dimensions=dimensions,
            time=time,
            domains=domains,
        )
        model_signal: ModelRouteSignal | None = None

        if rules.label == "refused":
            missing = next(
                (
                    phrase
                    for phrase, code in _OUT_OF_SCOPE_PHRASES
                    if code in rules.matched
                ),
                "the requested attribute",
            )
            return self._result(
                route_labels=("refused",),
                confidence=0.95,
                intent="Descriptive",
                slots=slots,
                signals=RouterSignals(
                    rules=rules,
                    classifier=classifier,
                ),
                reason=(
                    f"The catalog and governed registry do not cover {missing}. "
                    "The request is refused rather than answered from an "
                    "unrelated column."
                ),
            )

        if rules.label == "risk":
            return self._result(
                route_labels=("risk",),
                confidence=0.92,
                intent="Prescriptive",
                slots=slots,
                signals=RouterSignals(rules=rules, classifier=classifier),
                reason=(
                    "The question asks about customer or account risk, so it "
                    "is routed to risk analysis rather than SQL lookup."
                ),
            )

        if rules.label in ("anomaly", "driver"):
            if metric is not None and time.current is not None:
                labels: tuple[RouteLabel, ...]
                if rules.label == "driver":
                    labels = ("driver",)
                    reason = (
                        "The question asks what drove a metric, so it routes "
                        "to variance attribution."
                    )
                else:
                    labels = ("anomaly", "driver")
                    reason = (
                        "The question asks why a time-series metric changed, "
                        "so it routes to anomaly detection and driver "
                        "attribution."
                    )
                return self._result(
                    route_labels=labels,
                    confidence=0.95,
                    intent="Diagnostic",
                    slots=slots,
                    signals=RouterSignals(
                        rules=rules,
                        classifier=classifier,
                    ),
                    reason=reason,
                )
            understood = (
                f"the governed metric {metric.name} ({metric.metric_id})"
                if metric is not None
                else "a diagnostic question about the data"
            )
            needed = (
                "Please specify the time period to diagnose, such as a month, "
                "quarter or year."
                if metric is not None
                else "Please name the metric and the time period to diagnose."
            )
            return self._clarify(
                confidence=min(classifier.prob, 0.58),
                intent="Diagnostic",
                slots=slots,
                signals=RouterSignals(
                    rules=rules,
                    classifier=classifier,
                ),
                understood=f"You are asking about {understood}.",
                needed=needed,
                reason=(
                    "A diagnostic route is explicit, but the metric or time "
                    "window is incomplete."
                ),
            )

        if classifier.prob >= self._threshold:
            return self._result(
                route_labels=("lookup",),
                confidence=classifier.prob,
                intent=_intent_for_lookup(question),
                slots=slots,
                signals=RouterSignals(rules=rules, classifier=classifier),
                reason=(
                    "The catalog keywords and the governed registry identify "
                    "a lookup with enough confidence to answer directly."
                ),
            )

        if (
            metric is None
            and not domains
            and self._client is not None
        ):
            model_signal, model_decision = self._model_route(question, slots)
            if (
                model_decision is not None
                and model_decision.confidence >= self._threshold
            ):
                if model_decision.label == "clarify":
                    return self._clarify(
                        confidence=min(
                            classifier.prob, model_decision.confidence
                        ),
                        intent=model_decision.intent,
                        slots=model_decision.slots,
                        signals=RouterSignals(
                            rules=rules,
                            classifier=classifier,
                            model=model_signal,
                        ),
                        understood=(
                            "The question asks for a data answer but the "
                            "intent remains ambiguous."
                        ),
                        needed=(
                            "Please name the metric, time period and grouping "
                            "you want."
                        ),
                        reason=model_decision.reasoning
                        or "The local model requested a clarification.",
                    )
                if model_decision.label == "refused":
                    return self._result(
                        route_labels=("refused",),
                        confidence=model_decision.confidence,
                        intent=model_decision.intent,
                        slots=model_decision.slots,
                        signals=RouterSignals(
                            rules=rules,
                            classifier=classifier,
                            model=model_signal,
                        ),
                        reason=model_decision.reasoning
                        or "The local model refused the request.",
                    )
                return self._result(
                    route_labels=(model_decision.label,),
                    confidence=model_decision.confidence,
                    intent=model_decision.intent,
                    slots=model_decision.slots,
                    signals=RouterSignals(
                        rules=rules,
                        classifier=classifier,
                        model=model_signal,
                    ),
                    reason=model_decision.reasoning
                    or "The local model resolved the ambiguous intent.",
                )

        understood_metric = metric_phrase or (
            metric.name if metric is not None else None
        )
        if understood_metric is not None:
            understood = (
                f"You are asking about {understood_metric} "
                f"({metric.metric_id})."
                if metric is not None
                else f"You are asking about {understood_metric}."
            )
            needed = (
                "Please specify the time period, metric and comparison you "
                "want so the question can be answered precisely."
            )
        elif domains:
            understood = (
                "The question refers to the "
                + ", ".join(domains)
                + " data."
            )
            needed = (
                "Please name the metric, grouping and time period you want."
            )
        else:
            understood = (
                "The question is a request for a data answer, but no governed "
                "metric was identified."
            )
            needed = (
                "Please name the metric, the time period and any grouping "
                "dimension you want."
            )
        return self._clarify(
            confidence=min(classifier.prob, 0.69),
            intent=(
                "Diagnostic" if rules.label == "anomaly" else "Descriptive"
            ),
            slots=slots,
            signals=RouterSignals(
                rules=rules,
                classifier=classifier,
                model=model_signal,
            ),
            understood=understood,
            needed=needed,
            reason=(
                "The deterministic signals did not reach the routing "
                "confidence threshold."
            ),
        )

    def _blocked(
        self, question: str, guard_result: GuardResult
    ) -> RouterResult:
        return self._result(
            route_labels=("blocked",),
            confidence=1.0,
            intent="Descriptive",
            slots=RouteSlots(),
            signals=RouterSignals(),
            reason=(
                "The guard blocked the request before routing"
                + (
                    f" ({guard_result.reason}, rule {guard_result.matched_rule})."
                    if guard_result.reason
                    else "."
                )
            ),
        )

    def _model_route(
        self,
        question: str,
        base_slots: RouteSlots,
    ) -> tuple[ModelRouteSignal | None, _ModelDecision | None]:
        try:
            raw = self._client.chat_json(  # type: ignore[attr-defined]
                [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": question},
                ]
            )
        except Exception as exc:
            return (
                ModelRouteSignal(
                    reasoning=f"The local router model was unavailable: {exc}"
                ),
                None,
            )

        payload, usage = _payload_and_usage(raw, self._client)
        if not isinstance(payload, dict):
            return (
                ModelRouteSignal(
                    usage=usage,
                    reasoning="The router model did not return a JSON object.",
                ),
                None,
            )

        route_value = payload.get("route")
        if not isinstance(route_value, str) or route_value not in _ROUTES:
            return (
                ModelRouteSignal(
                    usage=usage,
                    reasoning="The router model proposed an unknown route.",
                ),
                None,
            )
        if route_value == "blocked":
            return (
                ModelRouteSignal(
                    usage=usage,
                    reasoning="The router model cannot issue a guard block.",
                ),
                None,
            )

        try:
            confidence = float(payload.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = min(max(confidence, 0.0), 1.0)
        metric_terms = _string_tuple(payload.get("metric_terms"))
        dimensions = _string_tuple(payload.get("dimensions"))
        time_expression = payload.get("time_expression")
        time = (
            _time_slot(time_expression, self._reference_date)
            if isinstance(time_expression, str) and time_expression.strip()
            else base_slots.time
        )
        model_slots = RouteSlots(
            metric_terms=metric_terms,
            dimensions=dimensions,
            filters=base_slots.filters,
            time=time,
            action_intent=bool(payload.get("action_intent", False)),
        )
        intent_value = payload.get("intent_label")
        if intent_value not in _INTENTS:
            intent_value = _default_intent(route_value)
        reasoning = payload.get("reasoning")
        signal = ModelRouteSignal(
            proposed_label=route_value,  # type: ignore[arg-type]
            slots=model_slots,
            confidence=confidence,
            reasoning=reasoning if isinstance(reasoning, str) else None,
            usage=usage,
        )
        decision = _ModelDecision(
            label=route_value,  # type: ignore[arg-type]
            intent=intent_value,  # type: ignore[arg-type]
            slots=model_slots,
            confidence=confidence,
            reasoning=signal.reasoning,
        )
        return signal, decision

    def _clarify(
        self,
        *,
        confidence: float,
        intent: IntentLabel,
        slots: RouteSlots,
        signals: RouterSignals,
        understood: str,
        needed: str,
        reason: str,
    ) -> RouterResult:
        confidence = min(max(confidence, 0.0), self._threshold - 0.01)
        return self._result(
            route_labels=("clarify",),
            confidence=confidence,
            intent=intent,
            slots=slots,
            signals=signals,
            reason=reason,
            clarification=ClarificationResult(
                what_understood=understood,
                what_needs_clarification=needed,
            ),
        )

    def _result(
        self,
        *,
        route_labels: tuple[RouteLabel, ...],
        confidence: float,
        intent: IntentLabel,
        slots: RouteSlots,
        signals: RouterSignals,
        reason: str,
        clarification: ClarificationResult | None = None,
    ) -> RouterResult:
        return RouterResult(
            route_labels=route_labels,
            confidence=confidence,
            intent_label=intent,
            slots=slots,
            signals=signals,
            reason=reason,
            clarification=clarification,
        )


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(
        str(item).strip()
        for item in value
        if isinstance(item, (str, int, float)) and str(item).strip()
    )


def _payload_and_usage(
    raw: object, client: object
) -> tuple[object, ModelUsage | None]:
    if isinstance(raw, tuple) and len(raw) == 2:
        payload, usage = raw
        return payload, usage if isinstance(usage, ModelUsage) else None
    if isinstance(raw, tuple) and len(raw) == 3:
        payload, tokens_in, tokens_out = raw
        name = str(getattr(client, "model", "router-model"))
        return payload, ModelUsage(
            name=name,
            version=name,
            tokens_in=max(int(tokens_in), 0),
            tokens_out=max(int(tokens_out), 0),
        )
    return raw, None


def _default_intent(route_value: str) -> IntentLabel:
    if route_value == "risk":
        return "Prescriptive"
    if route_value in ("anomaly", "driver"):
        return "Diagnostic"
    return "Descriptive"


__all__ = ["Router"]

"""Fail-closed answerability gate before SQL generation (ABS-1, ABS-2).

The checker is intentionally deterministic. Catalog terms and values act as a
first filter, retrieval confidence can override a keyword miss, and a small
labelled set of unsupported business attributes produces a useful refusal that
names the gap, lists covered domains and suggests the nearest answerable
question.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from contracts.models import AnswerabilityResult
from schema_retriever.catalog import SchemaCatalog

_STOPWORDS = frozenset(
    """
    a an and any are as at be been being by can could did do does each for from
    get give go had has have how i if in into is it its many me much my of on or
    our please show tell that the their them then there these they this those
    to was we were what when where which who why will with would you your
    """.split()
)

_GENERIC_COLUMN_TOKENS = frozenset(
    {
        "at",
        "code",
        "date",
        "id",
        "lenght",
        "length",
        "name",
        "prefix",
        "qty",
        "timestamp",
        "type",
        "unique",
        "zip",
    }
)

_ANALYTICAL_TOKENS = frozenset(
    """
    average avg mean median sum total count number percentage percent ratio
    top bottom highest lowest most least by per each monthly daily weekly
    yearly quarterly month months year years day days week weeks weekend
    weekends weekday weekdays quarter quarters q1 q2 q3 q4 hour hours minute
    minutes date dates time period last this previous current recent
    first new returning repeat active inactive trend distribution compare
    comparison growth change over across between during sold selling sell
    purchased purchase bought ordered placed made used using via through
    shipped paid late delayed early distinct different overall
    """.split()
)

_PHRASE_BOUNDARY = r"(?<!\w){phrase}(?!\w)"


@dataclass(frozen=True, slots=True)
class _Unsupported:
    label: str
    aliases: tuple[str, ...]
    suggestion: str


@dataclass(frozen=True, slots=True)
class _Synonym:
    phrase: str
    table: str
    column: str | None
    weight: float


@dataclass(frozen=True, slots=True)
class _Term:
    text: str
    weight: float
    kind: str
    table: str | None
    column: str | None


_UNSUPPORTED_ATTRIBUTES = (
    _Unsupported(
        label="profit",
        aliases=("profit", "profits", "profitability", "margin", "markup"),
        suggestion="What is total payment value by order status?",
    ),
    _Unsupported(
        label="inventory",
        aliases=("inventory", "stock", "stock level", "stock levels"),
        suggestion="How many products were sold by category?",
    ),
    _Unsupported(
        label="supplier",
        aliases=("supplier", "suppliers", "vendor", "vendors"),
        suggestion="Which sellers have the highest freight value?",
    ),
    _Unsupported(
        label="refund",
        aliases=("refund", "refunds", "chargeback", "chargebacks"),
        suggestion="How many orders have the status 'canceled'?",
    ),
    _Unsupported(
        label="employee/workforce",
        aliases=("employee", "employees", "staff", "headcount", "workforce"),
        suggestion="How many customers are in each state?",
    ),
    _Unsupported(
        label="marketing spend",
        aliases=(
            "marketing",
            "marketing spend",
            "ad spend",
            "advertising",
            "campaign",
            "campaigns",
        ),
        suggestion="What is total payment value by month?",
    ),
    _Unsupported(
        label="customer churn",
        aliases=("churn", "churned", "retention rate"),
        suggestion="How many customers were active last year?",
    ),
    _Unsupported(
        label="NPS",
        aliases=("nps", "net promoter score"),
        suggestion="What is the average review score?",
    ),
    _Unsupported(
        label="customer lifetime value",
        aliases=("customer lifetime value", "lifetime value", "ltv", "clv"),
        suggestion="What is total payment value by customer?",
    ),
    _Unsupported(
        label="customer acquisition cost",
        aliases=(
            "customer acquisition cost",
            "acquisition cost",
            "cac",
        ),
        suggestion="What is total payment value by month?",
    ),
    _Unsupported(
        label="conversion rate",
        aliases=("conversion rate", "conversion rates"),
        suggestion="What is the count of orders by order status?",
    ),
    _Unsupported(
        label="market share",
        aliases=("market share", "market shares"),
        suggestion="What is total payment value by seller?",
    ),
    _Unsupported(
        label="website traffic",
        aliases=("website traffic", "web traffic", "site visits"),
        suggestion="How many customers are in each state?",
    ),
    _Unsupported(
        label="returns",
        aliases=(
            "returns",
            "returned",
            "product return",
            "product returns",
            "return rate",
            "rma",
        ),
        suggestion="How many orders have the status 'canceled'?",
    ),
    _Unsupported(
        label="tax",
        aliases=("tax", "taxes", "vat", "sales tax"),
        suggestion="What is total payment value by order status?",
    ),
    _Unsupported(
        label="discount",
        aliases=(
            "discount",
            "discounts",
            "coupon",
            "coupons",
            "promo",
            "promotion",
            "promotions",
        ),
        suggestion="What is total payment value by order status?",
    ),
    _Unsupported(
        label="carrier",
        aliases=(
            "carrier",
            "carriers",
            "shipping carrier",
            "logistics provider",
            "logistics providers",
        ),
        suggestion="Which sellers have the highest freight value?",
    ),
    _Unsupported(
        label="country",
        aliases=("country", "countries", "nation", "nations"),
        suggestion="How many customers are in each state?",
    ),
    _Unsupported(
        label="customer demographics",
        aliases=(
            "age",
            "ages",
            "gender",
            "genders",
            "male",
            "males",
            "female",
            "females",
            "income",
            "education",
            "marital status",
        ),
        suggestion="What is the average review score by customer state?",
    ),
    _Unsupported(
        label="weather",
        aliases=(
            "weather",
            "temperature",
            "rain",
            "rainfall",
            "humidity",
            "climate",
        ),
        suggestion="What was the monthly order count last year?",
    ),
    _Unsupported(
        label="competitor",
        aliases=("competitor", "competitors", "competition", "competitions"),
        suggestion="What is total payment value by seller?",
    ),
    _Unsupported(
        label="holiday",
        aliases=("holiday", "holidays", "public holiday", "public holidays"),
        suggestion="What was the monthly order count last year?",
    ),
    _Unsupported(
        label="warehouse",
        aliases=(
            "warehouse",
            "warehouses",
            "fulfillment center",
            "fulfillment centers",
        ),
        suggestion="Which sellers have the highest freight value?",
    ),
    _Unsupported(
        label="fraud",
        aliases=("fraud", "fraudulent", "scam", "scams"),
        suggestion="How many orders have the status 'canceled'?",
    ),
    _Unsupported(
        label="forecast",
        aliases=(
            "forecast",
            "forecasts",
            "forecasting",
            "predict",
            "prediction",
            "predictions",
        ),
        suggestion="What was the monthly order count last year?",
    ),
)

_SYNONYMS = (
    _Synonym("revenue", "order_payments", "payment_value", 0.5),
    _Synonym("sales", "order_payments", "payment_value", 0.5),
    _Synonym("gmv", "order_payments", "payment_value", 0.5),
    _Synonym("buyer", "customers", None, 0.5),
    _Synonym("buyers", "customers", None, 0.5),
    _Synonym("state", "customers", "customer_state", 0.3),
    _Synonym("province", "customers", "customer_state", 0.3),
    _Synonym("payment method", "order_payments", "payment_type", 0.3),
    _Synonym("category", "products", "product_category_name", 0.3),
    _Synonym("rating", "order_reviews", "review_score", 0.4),
    _Synonym("shipping cost", "order_items", "freight_value", 0.4),
    _Synonym("freight", "order_items", "freight_value", 0.4),
    _Synonym("delivery time", "orders", "order_delivered_customer_date", 0.4),
    _Synonym("delivery delay", "orders", "order_estimated_delivery_date", 0.4),
    _Synonym("order date", "orders", "order_purchase_timestamp", 0.4),
    _Synonym("purchase date", "orders", "order_purchase_timestamp", 0.4),
    _Synonym("zip code", "customers", "customer_zip_code_prefix", 0.3),
    _Synonym("zip code", "sellers", "seller_zip_code_prefix", 0.3),
    _Synonym("zip code", "geolocation", "geolocation_zip_code_prefix", 0.3),
    _Synonym("postal code", "customers", "customer_zip_code_prefix", 0.3),
    _Synonym("cep", "customers", "customer_zip_code_prefix", 0.3),
    _Synonym("weight", "products", "product_weight_g", 0.3),
    _Synonym("product weight", "products", "product_weight_g", 0.3),
    _Synonym("length", "products", "product_length_cm", 0.3),
    _Synonym("height", "products", "product_height_cm", 0.3),
    _Synonym("width", "products", "product_width_cm", 0.3),
    _Synonym("photos", "products", "product_photos_qty", 0.3),
    _Synonym("installment", "order_payments", "payment_installments", 0.3),
    _Synonym("installments", "order_payments", "payment_installments", 0.3),
    _Synonym(
        "delivery date", "orders", "order_delivered_customer_date", 0.3
    ),
    _Synonym("approval date", "orders", "order_approved_at", 0.3),
    _Synonym(
        "shipping date", "orders", "order_delivered_carrier_date", 0.3
    ),
    _Synonym(
        "late delivery", "orders", "order_delivered_customer_date", 0.3
    ),
    _Synonym(
        "late deliveries", "orders", "order_delivered_customer_date", 0.3
    ),
)


def _normalize(text: str) -> str:
    lowered = text.lower().replace("&", " and ")
    lowered = re.sub(r"[_\-/]+", " ", lowered)
    lowered = re.sub(r"[^\w\s]", " ", lowered)
    return " ".join(lowered.split())


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(_normalize(text).split())


def _content_tokens(text: str) -> tuple[str, ...]:
    return tuple(token for token in _tokens(text) if token not in _STOPWORDS)


def _contains_phrase(text: str, phrase: str) -> bool:
    if not phrase:
        return False
    return (
        re.search(
            _PHRASE_BOUNDARY.format(phrase=re.escape(phrase)),
            text,
        )
        is not None
    )


def _humanize(name: str) -> str:
    return name.replace("_", " ").strip().lower()


class AnswerabilityChecker:
    """Decides whether the catalog can answer a question before SQL exists."""

    def __init__(
        self,
        catalog: SchemaCatalog,
        *,
        confidence_threshold: float = 0.4,
    ) -> None:
        if not 0.0 <= confidence_threshold <= 1.0:
            raise ValueError("confidence_threshold must be between 0 and 1")
        self._catalog = catalog
        self._confidence_threshold = confidence_threshold
        self._terms = self._build_terms()

    @property
    def catalog(self) -> SchemaCatalog:
        return self._catalog

    def check(
        self,
        question: str,
        *,
        retrieval_confidence: float | None = None,
    ) -> AnswerabilityResult:
        normalized = _normalize(question)
        if not normalized:
            raise ValueError("question must not be empty")
        if retrieval_confidence is not None and not 0.0 <= retrieval_confidence <= 1.0:
            raise ValueError("retrieval_confidence must be between 0 and 1")

        unsupported = [
            item
            for item in _UNSUPPORTED_ATTRIBUTES
            if any(
                _contains_phrase(normalized, _normalize(alias))
                for alias in item.aliases
            )
        ]
        if unsupported:
            labels = tuple(dict.fromkeys(item.label for item in unsupported))
            return AnswerabilityResult(
                answerable=False,
                confidence=0.99,
                missing=labels,
                closest_answerable=unsupported[0].suggestion,
                cheap_filter_hit=(
                    "unsupported_attribute:" + ", ".join(labels)
                ),
                available_domains=self._catalog.domains,
            )

        matched_terms = [
            term
            for term in self._terms
            if _contains_phrase(normalized, term.text)
        ]
        coverage = min(
            1.0,
            sum(term.weight for term in matched_terms),
        )
        unmatched = self._unmatched_terms(normalized)
        has_specific_match = any(
            term.kind in {"column", "value", "synonym"}
            for term in matched_terms
        )

        if unmatched and not has_specific_match and retrieval_confidence is None:
            return AnswerabilityResult(
                answerable=False,
                confidence=0.85,
                missing=unmatched,
                closest_answerable=self._generic_suggestion(),
                cheap_filter_hit="no_catalog_grounding",
                available_domains=self._catalog.domains,
            )

        if retrieval_confidence is None:
            answerable = coverage >= self._confidence_threshold
            confidence = coverage if answerable else 0.85
        else:
            answerable = (
                coverage >= self._confidence_threshold
                or retrieval_confidence >= self._confidence_threshold
            )
            confidence = (
                min(0.99, max(coverage, retrieval_confidence))
                if answerable
                else 0.85
            )

        if answerable:
            return AnswerabilityResult(
                answerable=True,
                confidence=round(confidence, 4),
                missing=(),
                closest_answerable=None,
                cheap_filter_hit=None,
                available_domains=self._catalog.domains,
            )

        missing = self._unknown_terms(normalized)
        return AnswerabilityResult(
            answerable=False,
            confidence=round(confidence, 4),
            missing=missing,
            closest_answerable=self._generic_suggestion(),
            cheap_filter_hit="no_catalog_grounding",
            available_domains=self._catalog.domains,
        )

    def _build_terms(self) -> tuple[_Term, ...]:
        terms: dict[tuple[str, str, str | None, str | None], _Term] = {}

        def add(
            text: str,
            weight: float,
            kind: str,
            table: str | None = None,
            column: str | None = None,
        ) -> None:
            normalized = _normalize(text)
            if not normalized or normalized in _STOPWORDS:
                return
            key = (normalized, kind, table, column)
            existing = terms.get(key)
            if existing is None or weight > existing.weight:
                terms[key] = _Term(
                    text=normalized,
                    weight=weight,
                    kind=kind,
                    table=table,
                    column=column,
                )

        for table in self._catalog.tables:
            add(table.name, 0.5, "table", table.name)
            for token in _content_tokens(table.name):
                add(token, 0.4, "table", table.name)

            for column in table.columns:
                add(column.name, 0.3, "column", table.name, column.name)
                for token in _content_tokens(column.name):
                    if token in _GENERIC_COLUMN_TOKENS:
                        continue
                    add(token, 0.2, "column_token", table.name, column.name)

                for value in column.values:
                    normalized_value = _normalize(value)
                    if (
                        not normalized_value
                        or normalized_value in _STOPWORDS
                        or normalized_value.isdigit()
                        or len(normalized_value) < 2
                    ):
                        continue
                    add(
                        normalized_value,
                        0.2,
                        "value",
                        table.name,
                        column.name,
                    )

        for synonym in _SYNONYMS:
            if self._catalog.get_table(synonym.table) is None:
                continue
            if (
                synonym.column is not None
                and self._catalog.get_column(
                    synonym.table, synonym.column
                )
                is None
            ):
                continue
            add(
                synonym.phrase,
                synonym.weight,
                "synonym",
                synonym.table,
                synonym.column,
            )

        return tuple(terms.values())

    def _unmatched_terms(self, normalized_question: str) -> tuple[str, ...]:
        known_tokens: set[str] = set()
        for term in self._terms:
            known_tokens.update(term.text.split())

        unknown = [
            token
            for token in _content_tokens(normalized_question)
            if token not in known_tokens
            and token not in _ANALYTICAL_TOKENS
            and not token.isdigit()
            and len(token) >= 3
        ]
        return tuple(dict.fromkeys(unknown))[:3]

    def _unknown_terms(self, normalized_question: str) -> tuple[str, ...]:
        return self._unmatched_terms(normalized_question) or (
            "no matching catalog entity",
        )

    def _generic_suggestion(self) -> str | None:
        for table in self._catalog.tables:
            for column in table.columns:
                if column.values and not column.name.lower().endswith("_id"):
                    return (
                        f"What is the count of {_humanize(table.name)} "
                        f"by {_humanize(column.name)}?"
                    )
        if self._catalog.tables:
            return f"Which {_humanize(self._catalog.tables[0].name)} are available?"
        return None


__all__ = ["AnswerabilityChecker"]

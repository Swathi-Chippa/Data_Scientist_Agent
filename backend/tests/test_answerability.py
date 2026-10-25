"""Tests for the fail-closed answerability gate (ABS-1, ABS-2)."""

import pytest
from pydantic import ValidationError

from answerability import AnswerabilityChecker
from contracts.models import AnswerabilityResult
from schema_retriever.catalog import CatalogColumn, CatalogTable, SchemaCatalog


def make_catalog() -> SchemaCatalog:
    return SchemaCatalog(
        version="test-v1",
        tables=(
            CatalogTable(
                name="orders",
                columns=(
                    CatalogColumn(
                        name="order_status",
                        data_type="VARCHAR",
                        values=(
                            "approved",
                            "canceled",
                            "delivered",
                            "invoiced",
                            "processing",
                            "shipped",
                            "unavailable",
                        ),
                    ),
                    CatalogColumn(
                        name="order_purchase_timestamp", data_type="VARCHAR"
                    ),
                    CatalogColumn(
                        name="order_delivered_customer_date",
                        data_type="VARCHAR",
                    ),
                    CatalogColumn(
                        name="order_estimated_delivery_date",
                        data_type="VARCHAR",
                    ),
                ),
            ),
            CatalogTable(
                name="customers",
                columns=(
                    CatalogColumn(
                        name="customer_state",
                        data_type="VARCHAR",
                        values=("MG", "RJ", "SP", "TO"),
                    ),
                    CatalogColumn(
                        name="customer_city",
                        data_type="VARCHAR",
                        values=("campinas", "rio de janeiro", "sao paulo"),
                    ),
                ),
            ),
            CatalogTable(
                name="order_payments",
                columns=(
                    CatalogColumn(
                        name="payment_type",
                        data_type="VARCHAR",
                        values=("boleto", "credit_card", "debit_card", "voucher"),
                    ),
                    CatalogColumn(name="payment_value", data_type="DOUBLE"),
                ),
            ),
            CatalogTable(
                name="products",
                columns=(
                    CatalogColumn(
                        name="product_category_name",
                        data_type="VARCHAR",
                        values=("auto", "health_beauty"),
                    ),
                    CatalogColumn(name="price", data_type="DOUBLE"),
                ),
            ),
            CatalogTable(
                name="order_reviews",
                columns=(
                    CatalogColumn(
                        name="review_score", data_type="BIGINT", values=("1", "5")
                    ),
                ),
            ),
        ),
    )


def test_answerable_question_is_grounded_in_the_catalog():
    result = AnswerabilityChecker(make_catalog()).check(
        "How many orders were delivered?"
    )

    assert result.answerable is True
    assert result.confidence >= 0.5
    assert result.missing == ()
    assert result.closest_answerable is None
    assert "orders" in result.available_domains


def test_refuses_known_missing_attribute_and_lists_available_domains():
    result = AnswerabilityChecker(make_catalog()).check(
        "What is the profit margin by customer state?"
    )

    assert result.answerable is False
    assert "profit" in result.missing
    assert result.closest_answerable is not None
    assert "payment value" in result.closest_answerable.lower()
    assert result.cheap_filter_hit is not None
    assert "profit" in result.cheap_filter_hit
    assert "orders" in result.available_domains
    assert "customers" in result.available_domains


def test_refuses_a_question_with_no_catalog_grounding():
    result = AnswerabilityChecker(make_catalog()).check(
        "What is the population of Paris tomorrow?"
    )

    assert result.answerable is False
    assert result.missing
    assert result.closest_answerable is not None
    assert result.available_domains
    assert result.cheap_filter_hit == "no_catalog_grounding"


def test_retrieval_confidence_can_back_a_keyword_miss():
    checker = AnswerabilityChecker(make_catalog())

    supported = checker.check("How is team morale?", retrieval_confidence=0.82)
    unsupported = checker.check("How is team morale?", retrieval_confidence=0.1)

    assert supported.answerable is True
    assert supported.confidence >= 0.8
    assert unsupported.answerable is False
    assert unsupported.missing


def test_known_missing_attribute_wins_over_high_retrieval_confidence():
    result = AnswerabilityChecker(make_catalog()).check(
        "What is profit by state?", retrieval_confidence=0.99
    )

    assert result.answerable is False
    assert "profit" in result.missing


def test_supported_synonyms_are_grounded():
    result = AnswerabilityChecker(make_catalog()).check(
        "What is revenue by customer state?"
    )

    assert result.answerable is True
    assert result.missing == ()


def test_plural_table_term_grounds_a_question_before_value_linking():
    result = AnswerabilityChecker(make_catalog()).check(
        "How many card payments?"
    )

    assert result.answerable is True
    assert result.missing == ()


def test_churn_refusal_suggests_an_answerable_alternative():
    checker = AnswerabilityChecker(make_catalog())

    result = checker.check("How many customers churned?")

    assert result.answerable is False
    assert "customer churn" in result.missing
    assert result.closest_answerable is not None
    assert checker.check(result.closest_answerable).answerable is True


def test_common_out_of_catalog_attributes_are_refused():
    checker = AnswerabilityChecker(make_catalog())
    cases = [
        ("How many orders were returned?", "returns"),
        ("What is the total tax on orders?", "tax"),
        ("How many orders used a discount?", "discount"),
        ("Which carrier shipped the most orders?", "carrier"),
        ("How many customers by country?", "country"),
        ("How many female customers are there?", "customer demographics"),
        ("How does weather affect orders?", "weather"),
        ("How do competitors affect sales?", "competitor"),
        ("How do holidays affect orders?", "holiday"),
        ("How many orders shipped from each warehouse?", "warehouse"),
        ("How many fraudulent payments are there?", "fraud"),
    ]

    for question, label in cases:
        result = checker.check(question)
        assert result.answerable is False, question
        assert label in result.missing, question


def test_unknown_qualifier_does_not_make_a_table_grounded_question_answerable():
    result = AnswerabilityChecker(make_catalog()).check(
        "How many orders used buy now pay later?"
    )

    assert result.answerable is False
    assert result.cheap_filter_hit == "no_catalog_grounding"
    assert result.missing


def test_analytical_and_time_words_do_not_trigger_grounding_refusal():
    checker = AnswerabilityChecker(make_catalog())

    for question in (
        "How many orders by month?",
        "What is the average order value?",
        "What are the top selling products?",
    ):
        assert checker.check(question).answerable is True, question


def test_known_missing_derived_metrics_are_refused():
    checker = AnswerabilityChecker(make_catalog())

    lifetime_value = checker.check("What is the customer lifetime value?")
    acquisition_cost = checker.check(
        "What is the customer acquisition cost?"
    )

    assert lifetime_value.answerable is False
    assert "customer lifetime value" in lifetime_value.missing
    assert acquisition_cost.answerable is False
    assert "customer acquisition cost" in acquisition_cost.missing


def test_common_business_synonyms_are_answerable():
    checker = AnswerabilityChecker(make_catalog())

    assert checker.check("What is the average rating?").answerable is True
    assert (
        checker.check("What is the average delivery time?").answerable is True
    )


def test_available_domains_are_sorted_and_human_readable():
    checker = AnswerabilityChecker(make_catalog())

    result = checker.check("What is profit?")

    assert result.available_domains == tuple(sorted(result.available_domains))
    assert "order payments" in result.available_domains
    assert "order reviews" in result.available_domains


def test_empty_questions_are_rejected():
    with pytest.raises(ValueError):
        AnswerabilityChecker(make_catalog()).check("   ")


def test_refusal_contract_requires_a_missing_attribute():
    with pytest.raises(ValidationError):
        AnswerabilityResult(answerable=False, confidence=0.9)


def test_answerability_confidence_is_bounded():
    with pytest.raises(ValidationError):
        AnswerabilityResult(answerable=True, confidence=1.1)

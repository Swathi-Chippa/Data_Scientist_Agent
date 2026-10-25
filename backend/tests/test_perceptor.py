"""Tests for fuzzy value linking in the schema perceptor (GRD-8)."""

import pytest

from contracts.models import PerceptorConfig
from schema_retriever.catalog import CatalogColumn, CatalogTable, SchemaCatalog
from schema_retriever.perceptor import SchemaPerceptor


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
                name="sellers",
                columns=(
                    CatalogColumn(
                        name="seller_state",
                        data_type="VARCHAR",
                        values=("MG", "RJ", "SP", "TO"),
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
                ),
            ),
            CatalogTable(
                name="order_reviews",
                columns=(
                    CatalogColumn(
                        name="review_score",
                        data_type="BIGINT",
                        values=("1", "5"),
                    ),
                ),
            ),
        ),
    )


def _matches(result, *, column=None, literal=None):
    return [
        value
        for value in result.linked_values
        if (column is None or value.column == column)
        and (literal is None or value.literal == literal)
    ]


def test_links_exact_state_code_in_candidate_table():
    result = SchemaPerceptor(make_catalog()).perceive(
        "How many customers from SP?", tables={"customers"}
    )

    matches = _matches(result, column="customer_state", literal="SP")
    assert len(matches) == 1
    assert matches[0].table == "customers"
    assert matches[0].phrase == "sp"
    assert matches[0].confidence >= 0.9
    assert result.requires_clarification is False
    assert result.catalog_version == "test-v1"


def test_links_multiword_payment_type():
    result = SchemaPerceptor(make_catalog()).perceive(
        "Show me credit card payments"
    )

    matches = _matches(result, column="payment_type", literal="credit_card")
    assert len(matches) == 1
    assert matches[0].table == "order_payments"
    assert matches[0].confidence >= 0.9


def test_links_fuzzy_typo_to_payment_type():
    result = SchemaPerceptor(make_catalog()).perceive("credit crd payments")

    matches = _matches(result, column="payment_type", literal="credit_card")
    assert len(matches) == 1
    assert matches[0].confidence >= 0.55


def test_does_not_link_common_word_to_two_letter_state_code():
    result = SchemaPerceptor(make_catalog()).perceive("Show orders to customers")

    assert _matches(result, literal="TO") == []


def test_lowercase_state_code_still_links():
    result = SchemaPerceptor(make_catalog()).perceive(
        "customers from sp", tables={"customers"}
    )

    assert _matches(result, column="customer_state", literal="SP")


def test_ambiguous_card_value_requests_clarification():
    result = SchemaPerceptor(make_catalog()).perceive("How many card payments?")

    literals = {value.literal for value in _matches(result, column="payment_type")}
    assert {"credit_card", "debit_card"} <= literals
    assert result.requires_clarification is True
    assert result.clarification_reason is not None
    assert "credit_card" in result.clarification_reason
    assert "debit_card" in result.clarification_reason


def test_most_relevant_table_ranks_first():
    result = SchemaPerceptor(make_catalog()).perceive(
        "Which sellers are from SP?"
    )

    assert result.linked_values
    assert result.linked_values[0].table == "sellers"


def test_candidate_table_filter_excludes_other_tables():
    result = SchemaPerceptor(make_catalog()).perceive("SP", tables={"sellers"})

    assert result.linked_values
    assert {value.table for value in result.linked_values} == {"sellers"}


def test_qualified_column_hint_disambiguates_state():
    matches = SchemaPerceptor(make_catalog()).link(
        "SP", column_hint="customers.customer_state"
    )

    assert len(matches) == 1
    assert matches[0].table == "customers"
    assert matches[0].column == "customer_state"
    assert matches[0].literal == "SP"


def test_unknown_candidate_table_is_rejected():
    with pytest.raises(ValueError):
        SchemaPerceptor(make_catalog()).perceive("SP", tables={"missing"})


def test_numeric_values_are_not_treated_as_categorical_literals():
    result = SchemaPerceptor(make_catalog()).perceive("Show 5 star reviews")

    assert _matches(result, column="review_score") == []


def test_does_not_fuzzy_link_a_metric_word_to_a_related_status_value():
    result = SchemaPerceptor(make_catalog()).perceive(
        "What is the average delivery time by state?"
    )

    assert _matches(result, column="order_status") == []
    assert result.requires_clarification is False


def test_threshold_filters_weak_match_and_requests_clarification():
    perceptor = SchemaPerceptor(
        make_catalog(), config=PerceptorConfig(value_score_threshold=0.99)
    )

    result = perceptor.perceive("credit crd payments")

    assert result.linked_values == ()
    assert result.requires_clarification is True
    assert result.clarification_reason is not None


def test_empty_questions_are_rejected():
    with pytest.raises(ValueError):
        SchemaPerceptor(make_catalog()).perceive("   ")

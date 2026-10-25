"""Tests for deterministic NL2SQL generation (SQL-1, SQL-5, SQL-16)."""

import pytest

from contracts.models import LinkedValue, SqlCandidate
from schema_retriever.catalog import (
    CatalogColumn,
    CatalogTable,
    JoinGraph,
    SchemaCatalog,
)
from sql.generator import SQLGenerationError, SQLGenerator
from sql.validator import SQLValidator


def sample_catalog() -> SchemaCatalog:
    return SchemaCatalog(
        version="generator-test",
        tables=(
            CatalogTable(
                name="customers",
                columns=(
                    CatalogColumn(name="customer_id", data_type="TEXT"),
                    CatalogColumn(
                        name="customer_state",
                        data_type="TEXT",
                        values=("SP", "RJ"),
                    ),
                    CatalogColumn(name="customer_city", data_type="TEXT"),
                ),
            ),
            CatalogTable(
                name="orders",
                columns=(
                    CatalogColumn(name="order_id", data_type="TEXT"),
                    CatalogColumn(name="customer_id", data_type="TEXT"),
                    CatalogColumn(name="order_status", data_type="TEXT"),
                    CatalogColumn(
                        name="order_purchase_timestamp", data_type="TIMESTAMP"
                    ),
                    CatalogColumn(
                        name="order_delivered_customer_date", data_type="TIMESTAMP"
                    ),
                ),
            ),
            CatalogTable(
                name="order_items",
                columns=(
                    CatalogColumn(name="order_id", data_type="TEXT"),
                    CatalogColumn(name="product_id", data_type="TEXT"),
                    CatalogColumn(name="price", data_type="REAL"),
                    CatalogColumn(name="freight_value", data_type="REAL"),
                ),
            ),
            CatalogTable(
                name="products",
                columns=(
                    CatalogColumn(name="product_id", data_type="TEXT"),
                    CatalogColumn(
                        name="product_category_name", data_type="TEXT"
                    ),
                ),
            ),
            CatalogTable(
                name="order_payments",
                columns=(
                    CatalogColumn(name="order_id", data_type="TEXT"),
                    CatalogColumn(name="payment_type", data_type="TEXT"),
                    CatalogColumn(name="payment_value", data_type="REAL"),
                ),
            ),
        ),
    )


def validator_for(result) -> SQLValidator:
    return SQLValidator(
        result.allowlist.tables,
        dialect="sqlite",
        mode="enterprise",
        columns=[
            f"{column.table}.{column.column}"
            for column in result.allowlist.columns
        ],
    )


def test_join_graph_infers_foreign_key_paths_from_the_catalog():
    graph = JoinGraph.from_catalog(sample_catalog())

    path = graph.find_path("order_items", "customers")

    assert path is not None
    assert [edge.edge_id for edge in path] == [
        "order_items.order_id=orders.order_id",
        "customers.customer_id=orders.customer_id",
    ]
    assert graph.find_path("customers", "customers") == ()


def test_generates_cte_skeleton_with_join_predicates_and_allowlist():
    generator = SQLGenerator(sample_catalog(), dialect="sqlite")

    result = generator.generate(
        "What is the average delivery time by customer state?"
    )

    sql = result.candidate.sql
    assert isinstance(result.candidate, SqlCandidate)
    assert result.candidate.origin == "generator"
    assert result.candidate.dialect == "sqlite"
    assert sql.lstrip().upper().startswith("WITH")
    assert "JOIN" in sql.upper()
    assert "ON" in sql.upper()
    assert "AVG(" in sql.upper()
    assert "JULIANDAY" in sql.upper()
    assert "LIMIT" in sql.upper()
    assert result.candidate.uses_edges == (
        "customers.customer_id=orders.customer_id",
    )
    assert "customer_state" in result.candidate.grain
    assert {column.name for column in result.candidate.intended_columns} >= {
        "state",
        "avg_delivery_days",
    }
    assert "orders" in result.allowlist.tables
    assert "customers" in result.allowlist.tables
    assert any(
        column.table == "orders"
        and column.column == "order_delivered_customer_date"
        for column in result.allowlist.columns
    )

    validation = validator_for(result).validate(sql)
    assert validation.approved is True, validation


def test_generates_scalar_count_with_filter_and_scalar_limit():
    generator = SQLGenerator(sample_catalog(), dialect="sqlite")

    result = generator.generate("How many orders were delivered?")

    sql = result.candidate.sql.upper()
    assert "COUNT(*)" in sql
    assert "ORDER_STATUS = 'DELIVERED'" in sql
    assert "WITH" not in sql
    assert "LIMIT 1" in sql
    assert any(
        column.name == "order_count" and column.role == "measure"
        for column in result.candidate.intended_columns
    )
    assert validator_for(result).validate(result.candidate.sql).approved is True


def test_deduplicates_status_and_linked_value_filters():
    generator = SQLGenerator(sample_catalog(), dialect="sqlite")

    result = generator.generate(
        "How many delivered orders?",
        linked_values=(
            LinkedValue(
                phrase="delivered",
                table="orders",
                column="order_status",
                literal="delivered",
                confidence=1.0,
            ),
        ),
    )

    sql = result.candidate.sql.upper()
    assert sql.count("ORDER_STATUS = 'DELIVERED'") == 1


def test_respects_top_n_and_orders_grouped_results():
    generator = SQLGenerator(sample_catalog(), dialect="sqlite")

    result = generator.generate(
        "What are the top 3 customer states by number of orders?"
    )

    sql = result.candidate.sql
    assert "LIMIT 3" in sql.upper()
    assert "ORDER BY" in sql.upper()
    assert "COUNT(*)" in sql.upper()
    assert "GROUP BY" in sql.upper()
    assert result.candidate.uses_edges == (
        "customers.customer_id=orders.customer_id",
    )
    assert validator_for(result).validate(sql).approved is True


def test_linked_values_become_parameterised_literal_filters():
    generator = SQLGenerator(sample_catalog(), dialect="sqlite")
    linked = LinkedValue(
        phrase="SP",
        table="customers",
        column="customer_state",
        literal="SP",
        confidence=1.0,
    )

    result = generator.generate(
        "How many orders from SP?", linked_values=(linked,)
    )

    sql = result.candidate.sql
    assert "customer_state = 'SP'" in sql
    assert "JOIN" in sql.upper()
    assert validator_for(result).validate(sql).approved is True


def test_unmappable_question_returns_structured_generation_error():
    generator = SQLGenerator(sample_catalog(), dialect="sqlite")

    with pytest.raises(SQLGenerationError) as exc_info:
        generator.generate("What is the airspeed velocity of a swallow?")

    assert exc_info.value.what_understood
    assert exc_info.value.what_needs_clarification

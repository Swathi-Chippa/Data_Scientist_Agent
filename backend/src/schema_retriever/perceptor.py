"""Fuzzy value linking for schema perception (GRD-8).

The perceptor maps phrases in a question to exact literals stored in
low-cardinality catalog columns. It is deterministic, works before any LLM
call, and reports low-confidence or ambiguous links so the caller can clarify
instead of generating SQL from a guess.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Iterable

from contracts.models import LinkedValue, PerceptionResult, PerceptorConfig
from schema_retriever.catalog import CatalogColumn, SchemaCatalog

_STOPWORDS = frozenset(
    """
    a an and any are as at average be been being by can count could data did do
    does each for from get give go had has have how i if in info information
    into is it its least list many me most much my number of on or our per
    please record records row rows show sum table tables tell that the their
    them then there these they this those to top total up us was we were what
    when where which who why will with would you your
    """.split()
)

_PHRASE_BOUNDARY = r"(?<!\w){phrase}(?!\w)"
_VALUE_DISCRIMINATION_MARGIN = 0.05
_LOW_CONFIDENCE = 0.78
_CLARIFICATION_FLOOR_RATIO = 0.7


@dataclass(frozen=True, slots=True)
class _Match:
    confidence: float
    phrase: str


def _normalize(text: str) -> str:
    lowered = text.lower().replace("&", " and ")
    lowered = re.sub(r"[_\-/]+", " ", lowered)
    lowered = re.sub(r"[^\w\s]", " ", lowered)
    return " ".join(lowered.split())


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(_normalize(text).split())


def _content_tokens(text_or_tokens: str | Iterable[str]) -> tuple[str, ...]:
    tokens = (
        _tokens(text_or_tokens)
        if isinstance(text_or_tokens, str)
        else tuple(text_or_tokens)
    )
    return tuple(token for token in tokens if token not in _STOPWORDS)


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


def _trigram_similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0

    left_padded = f" {left} "
    right_padded = f" {right} "
    left_counts = Counter(
        left_padded[index : index + 3]
        for index in range(len(left_padded) - 2)
    )
    right_counts = Counter(
        right_padded[index : index + 3]
        for index in range(len(right_padded) - 2)
    )
    shared = sum((left_counts & right_counts).values())
    total = sum(left_counts.values()) + sum(right_counts.values())
    if total == 0:
        return 0.0
    return 2.0 * shared / total


def _ngrams(tokens: tuple[str, ...], size: int) -> tuple[str, ...]:
    if not tokens:
        return ()
    if len(tokens) <= size:
        return (" ".join(tokens),)
    return tuple(
        " ".join(tokens[index : index + size])
        for index in range(len(tokens) - size + 1)
    )


def _score_value(
    value: str,
    question_normalized: str,
    question_content: tuple[str, ...],
) -> _Match | None:
    value_normalized = _normalize(value)
    if (
        not value_normalized
        or value_normalized.isdigit()
        or value_normalized.replace(" ", "").isdigit()
    ):
        return None

    value_tokens = tuple(value_normalized.split())
    if len(value_tokens) == 1 and value_normalized in _STOPWORDS:
        return None

    if _contains_phrase(question_normalized, value_normalized):
        return _Match(1.0, value_normalized)

    value_content = _content_tokens(value_tokens)
    question_content_text = " ".join(question_content)
    value_content_text = " ".join(value_content)
    if value_content_text and _contains_phrase(
        question_content_text, value_content_text
    ):
        return _Match(0.97, value_content_text)

    question_set = set(question_content)
    value_set = set(value_content)
    overlap_count = len(value_set & question_set)
    overlap_ratio = overlap_count / len(value_set) if value_set else 0.0

    best_confidence = 0.0
    best_phrase = value_normalized
    if overlap_count:
        best_confidence = 0.55 + 0.35 * overlap_ratio
        best_phrase = " ".join(
            token for token in value_tokens if token in question_set
        )

    lengths = {
        max(1, len(value_tokens) - 1),
        len(value_tokens),
        len(value_tokens) + 1,
    }
    for size in sorted(lengths):
        for candidate in _ngrams(question_content, size):
            similarity = _trigram_similarity(value_normalized, candidate)
            if similarity > best_confidence:
                best_confidence = similarity
                best_phrase = candidate

    if best_confidence <= 0.0:
        return None
    if (
        overlap_count == 0
        and len(value_tokens) == 1
        and best_confidence < _LOW_CONFIDENCE
    ):
        return None
    return _Match(best_confidence, best_phrase)


def _stem(token: str) -> str:
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _column_relevance(
    table: str,
    column: str,
    question_content: tuple[str, ...],
) -> int:
    question_stems = {_stem(token) for token in question_content}
    column_words = set(_content_tokens(_tokens(f"{table} {column}")))
    return len({_stem(word) for word in column_words} & question_stems)


def _value_priority(value: str, question_content: tuple[str, ...]) -> int:
    value_tokens = set(_content_tokens(_tokens(value)))
    return 0 if value_tokens & set(question_content) else 1


class SchemaPerceptor:
    """Grounds question phrases to exact values in the catalog."""

    def __init__(
        self,
        catalog: SchemaCatalog,
        config: PerceptorConfig | None = None,
    ) -> None:
        self._catalog = catalog
        self._config = config or PerceptorConfig()

    @property
    def catalog(self) -> SchemaCatalog:
        return self._catalog

    def perceive(
        self,
        question: str,
        *,
        tables: Iterable[str] | None = None,
    ) -> PerceptionResult:
        normalized = _normalize(question)
        if not normalized:
            raise ValueError("question must not be empty")

        selected = self._select_tables(tables)
        question_content = _content_tokens(_tokens(normalized))
        table_relevance = {
            table.name: _column_relevance(
                table.name, "", question_content
            )
            for table in selected
        }

        candidates: list[
            tuple[int, str, str, CatalogColumn]
        ] = []
        for table in selected:
            for column in table.columns:
                if not column.values:
                    continue
                candidates.append(
                    (
                        _column_relevance(
                            table.name, column.name, question_content
                        ),
                        table.name,
                        column.name,
                        column,
                    )
                )

        candidates.sort(key=lambda item: (-item[0], item[1], item[2]))
        ordered_values = [
            (
                table_name,
                column.name,
                tuple(
                    sorted(
                        column.values,
                        key=lambda value: (
                            _value_priority(value, question_content),
                            value.casefold(),
                        ),
                    )
                ),
            )
            for _, table_name, _, column in candidates
        ]

        best_by_column: dict[tuple[str, str], dict[str, _Match]] = {}
        best_candidate: tuple[float, str, str, str] | None = None
        calls = 0
        index = 0

        while calls < self._config.value_search_max_calls:
            progressed = False
            for table_name, column_name, values in ordered_values:
                if index >= len(values):
                    continue

                progressed = True
                calls += 1
                literal = values[index]
                match = _score_value(literal, normalized, question_content)
                if match is not None:
                    if best_candidate is None or match.confidence > best_candidate[0]:
                        best_candidate = (
                            match.confidence,
                            table_name,
                            column_name,
                            literal,
                        )
                    if match.confidence >= self._config.value_score_threshold:
                        column_key = (table_name, column_name)
                        existing = best_by_column.setdefault(column_key, {})
                        current = existing.get(literal)
                        if current is None or match.confidence > current.confidence:
                            existing[literal] = match

                if calls >= self._config.value_search_max_calls:
                    break

            if not progressed:
                break
            index += 1

        linked: list[LinkedValue] = []
        ambiguous: list[str] = []
        for (table_name, column_name), literal_matches in best_by_column.items():
            ranked = sorted(
                literal_matches.items(),
                key=lambda item: (-item[1].confidence, item[0].casefold()),
            )
            top_confidence = ranked[0][1].confidence
            kept = [
                (literal, match)
                for literal, match in ranked
                if match.confidence >= top_confidence - _VALUE_DISCRIMINATION_MARGIN
            ][: self._config.value_description_top_k]

            if len(kept) > 1:
                literals = ", ".join(literal for literal, _ in kept)
                ambiguous.append(
                    f"Multiple values match '{kept[0][1].phrase}' in "
                    f"{table_name}.{column_name}: {literals}"
                )

            for literal, match in kept:
                linked.append(
                    LinkedValue(
                        phrase=match.phrase,
                        table=table_name,
                        column=column_name,
                        literal=literal,
                        confidence=round(match.confidence, 4),
                    )
                )

        linked.sort(
            key=lambda value: (
                -value.confidence,
                -table_relevance.get(value.table, 0),
                value.table,
                value.column,
                value.literal.casefold(),
            )
        )

        requires_clarification = bool(ambiguous)
        reason = "; ".join(ambiguous) if ambiguous else None

        if linked and not requires_clarification and linked[0].confidence < _LOW_CONFIDENCE:
            requires_clarification = True
            reason = (
                f"Low-confidence value match for '{linked[0].phrase}' "
                f"({linked[0].confidence:.2f} < {_LOW_CONFIDENCE:.2f})"
            )
        elif (
            not linked
            and best_candidate is not None
            and best_candidate[0]
            >= self._config.value_score_threshold * _CLARIFICATION_FLOOR_RATIO
        ):
            requires_clarification = True
            reason = (
                "No value match above confidence threshold for "
                f"'{best_candidate[3]}' (best {best_candidate[0]:.2f} < "
                f"{self._config.value_score_threshold:.2f})"
            )

        return PerceptionResult(
            catalog_version=self._catalog.version,
            linked_values=tuple(linked),
            requires_clarification=requires_clarification,
            clarification_reason=reason,
        )

    def link(
        self,
        phrase: str,
        *,
        column_hint: str | None = None,
        tables: Iterable[str] | None = None,
    ) -> tuple[LinkedValue, ...]:
        table_hint: str | None = None
        column_filter: str | None = None

        if column_hint is not None:
            hint = column_hint.strip()
            parts = [part.strip() for part in hint.split(".")]
            if not hint or any(not part for part in parts) or len(parts) > 2:
                raise ValueError(
                    "column_hint must be 'column', 'table.column', or a table name"
                )
            if len(parts) == 2:
                table_hint, column_filter = parts
            elif hint.lower() in {
                name.lower() for name in self._catalog.table_names
            }:
                table_hint = hint
            else:
                column_filter = hint

        if table_hint is not None and tables is not None:
            requested = {name.lower() for name in tables}
            if table_hint.lower() not in requested:
                raise ValueError(
                    "column_hint table is not in the requested candidate tables"
                )

        result = self.perceive(
            phrase,
            tables={table_hint} if table_hint is not None else tables,
        )
        if column_filter is None:
            return result.linked_values
        lowered = column_filter.lower()
        return tuple(
            value
            for value in result.linked_values
            if value.column.lower() == lowered
        )

    def _select_tables(self, tables: Iterable[str] | None):
        if tables is None:
            return self._catalog.tables

        requested = tuple(dict.fromkeys(tables))
        known = {name.lower() for name in self._catalog.table_names}
        unknown = [name for name in requested if name.lower() not in known]
        if unknown:
            raise ValueError(f"unknown catalog table(s): {', '.join(unknown)}")
        return self._catalog.get_tables(requested)


__all__ = ["SchemaPerceptor"]

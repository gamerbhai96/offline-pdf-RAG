"""
Comprehensive Regression Test Suite — Phase 14 Accuracy Repairs

Verifies all 10 mandatory architectural corrections:
1. Question routing never returns NO_ANSWER from retrieval preview.
2. Table override logic removed (presence of tab in preview does NOT automatically route to TABLE).
3. Strict phrase match signal works.
4. Definition section boost works (rewards dedicated entity section, penalizes generic intro).
5. Heading vs content distinction works (table header score capped at 0.10).
6. Evidence validation rejects missing attributes with requested_attribute_missing_from_document.
7. Fact_QA outputs clean, direct definitions without header noise.
8. List queries produce clean lists free of replacement character artifacts.
9. Table hints produce clean tables.
10. Citations are accurate, formatted as [Source: title | Page X], and deduplicated.
"""

import pytest
from dataclasses import dataclass
from typing import Optional

from core.question.analyzer import (
    NormalizedQuery, Entity, QuestionType, FormatHint, QuestionAnalyzer
)
from core.question.router import QuestionRouter, Route, AnswerFormat, RetrievalPreview
from core.retrieval.hybrid import (
    RetrievedChunk, DeterministicRanker, EvidenceValidator, ValidatedEvidence
)
from core.qa.answer_engine import (
    AnswerBuilder, SafePresentationEngine, ConfidenceEngine, ConfidenceLevel
)
from core.citation.engine import CitationEngine


class TestAccuracyRepairsRegression:

    # 1. Question routing never returns NO_ANSWER
    def test_1_question_routing_never_returns_no_answer(self):
        router = QuestionRouter()
        query = NormalizedQuery(
            raw_query="What is quantum entanglement?",
            normalized_query="what is quantum entanglement?",
            resolved_query="what is quantum entanglement?",
            question_type=QuestionType.DEFINITION,
        )
        preview = RetrievalPreview(has_table_evidence=False, evidence_count=0, top_score=0.0)
        decision = router.route(query, preview)
        assert decision.route != Route.NO_ANSWER
        assert decision.route == Route.FACT_QA

    # 2. Table override logic removed
    def test_2_table_override_logic_removed(self):
        router = QuestionRouter()
        query = NormalizedQuery(
            raw_query="What is Hive?",
            normalized_query="what is hive?",
            resolved_query="what is hive?",
            question_type=QuestionType.DEFINITION,
            format_hint=None,
        )
        preview = RetrievalPreview(has_table_evidence=True, evidence_count=5, top_score=0.8)
        decision = router.route(query, preview)
        assert decision.route == Route.FACT_QA
        assert decision.route != Route.TABLE

    # 3. Strict phrase match works
    def test_3_strict_phrase_match_works(self):
        ranker = DeterministicRanker()
        query = NormalizedQuery(
            raw_query="string functions",
            normalized_query="string functions",
            resolved_query="string functions",
            target_attribute="string functions",
        )
        c1 = RetrievedChunk(
            chunk_id="c1", document_id="doc1", page_id="16",
            text="Built-in String Functions: length, reverse, concat",
            dense_score=0.5, bm25_score=0.5, fusion_score=0.5
        )
        c2 = RetrievedChunk(
            chunk_id="c2", document_id="doc1", page_id="16",
            text="Hive supports functions for string manipulation and data processing",
            dense_score=0.5, bm25_score=0.5, fusion_score=0.5
        )
        reranked = ranker.rerank(query, [c1, c2])
        assert reranked[0].chunk_id == "c1"
        assert reranked[0].phrase_match > reranked[1].phrase_match

    # 4. Definition section boost works
    def test_4_definition_section_boost_works(self):
        ranker = DeterministicRanker()
        query = NormalizedQuery(
            raw_query="What is HQL?",
            normalized_query="what is hql?",
            resolved_query="what is hql?",
            entities=[Entity(text="HQL", etype="TECH", start=8, end=11)],
            target_attribute="definition",
        )
        c_intro = RetrievedChunk(
            chunk_id="c_intro", document_id="doc1", page_id="1",
            text="Unit IV: Apache Hive – HQL\n1. Introduction to Apache Hive\nApache Hive is a data warehouse...",
            dense_score=0.6, bm25_score=0.6, fusion_score=0.6
        )
        c_hql = RetrievedChunk(
            chunk_id="c_hql", document_id="doc1", page_id="2",
            text="1.2 Hive Query Language (HQL)\nHive Query Language (HQL) is a SQL-like query language provided by Hive...",
            dense_score=0.6, bm25_score=0.6, fusion_score=0.6
        )
        reranked = ranker.rerank(query, [c_intro, c_hql])
        assert reranked[0].chunk_id == "c_hql"
        assert reranked[0].section_score > reranked[1].section_score

    # 5. Heading vs content distinction works
    def test_5_heading_vs_content_distinction_works(self):
        ranker = DeterministicRanker()
        query = NormalizedQuery(
            raw_query="hive functions",
            normalized_query="hive functions",
            resolved_query="hive functions",
            target_attribute="functions",
        )
        c_table_header = RetrievedChunk(
            chunk_id="c_table_header", document_id="doc1", page_id="16",
            text="Fig 10: Built-in String Functions Table\nTable showing string functions",
            dense_score=0.5, bm25_score=0.5, fusion_score=0.5
        )
        c_section = RetrievedChunk(
            chunk_id="c_section", document_id="doc1", page_id="16",
            text="3. Built-in String Functions\nString functions in Hive are used to manipulate, format...",
            dense_score=0.5, bm25_score=0.5, fusion_score=0.5
        )
        reranked = ranker.rerank(query, [c_table_header, c_section])
        assert reranked[0].chunk_id == "c_section"
        assert reranked[1].heading_score <= 0.10   # table header capped at 0.10

    # 6. Evidence validation rejects missing attributes
    def test_6_evidence_validation_rejects_missing_attributes(self):
        validator = EvidenceValidator()
        query = NormalizedQuery(
            raw_query="What is quantum entanglement in Hive?",
            normalized_query="what is quantum entanglement in hive?",
            resolved_query="what is quantum entanglement in hive?",
            entities=[Entity(text="Hive", etype="TECH", start=31, end=35)],
            target_attribute="definition",
        )
        chunks = [
            RetrievedChunk(
                chunk_id="c1", document_id="doc1", page_id="1",
                text="Apache Hive is a data warehouse and ETL tool built on top of Hadoop.",
                dense_score=0.7, bm25_score=0.7, fusion_score=0.7
            )
        ]
        validated, rejected = validator.validate(query, chunks)
        assert len(validated) == 0
        assert len(rejected) == 1
        assert rejected[0]["rejection_reason"] == "missing_core_entity"

    # 7. Fact_QA outputs correct definitions
    def test_7_fact_qa_outputs_correct_definitions(self):
        builder = AnswerBuilder()
        ev = [
            ValidatedEvidence(
                chunk_id="c1", document_id="doc1", page_id="1",
                text="Unit IV: Apache Hive – HQL\n1. Introduction to Apache Hive\nApache Hive is a data warehouse and ETL tool built on top of Hadoop.",
                dense_score=0.9, bm25_score=0.9, fusion_score=0.9, validation_score=0.9, validation_passed=True
            )
        ]
        query = NormalizedQuery(raw_query="What is Hive?", normalized_query="what is hive?", resolved_query="what is hive?", question_type=QuestionType.DEFINITION)
        route = QuestionRouter().route(query)
        draft = builder.build("What is Hive?", ev, route)
        assert draft is not None
        pt = draft.all_points()[0].text
        assert pt.startswith("Apache Hive is a data warehouse")
        assert "Unit IV:" not in pt

    # 8. List queries produce clean lists
    def test_8_list_queries_produce_clean_lists(self):
        builder = AnswerBuilder()
        ev = [
            ValidatedEvidence(
                chunk_id="c1", document_id="doc1", page_id="16",
                text="\uFFFD length(str) : Returns the length of the string.\n\uFFFD reverse(str) : Returns the reversed string.",
                dense_score=0.9, bm25_score=0.9, fusion_score=0.9, validation_score=0.9, validation_passed=True
            )
        ]
        query = NormalizedQuery(raw_query="What are the string functions in Hive?", normalized_query="what are the string functions in hive?", resolved_query="what are the string functions in hive?", question_type=QuestionType.LIST)
        route = QuestionRouter().route(query)
        draft = builder.build("What are the string functions in Hive?", ev, route)
        formatted = SafePresentationEngine().format(draft, route, ConfidenceEngine().score(ev, True, 1.0, True))
        plain = formatted.plain_text()
        assert "\uFFFD" not in plain
        assert "length(str)" in plain
        assert "reverse(str)" in plain

    # 9. Table hints produce clean tables
    def test_9_table_hints_produce_clean_tables(self):
        builder = AnswerBuilder()
        ev = [
            ValidatedEvidence(
                chunk_id="c1", document_id="doc1", page_id="16",
                text="length(str) : Returns string length\nreverse(str) : Returns reversed string",
                dense_score=0.9, bm25_score=0.9, fusion_score=0.9, validation_score=0.9, validation_passed=True
            )
        ]
        query = NormalizedQuery(
            raw_query="Give the string functions in Hive in a table.",
            normalized_query="give the string functions in hive in a table.",
            resolved_query="give the string functions in hive in a table.",
            format_hint=FormatHint.TABLE,
            question_type=QuestionType.LIST
        )
        route = QuestionRouter().route(query)
        assert route.route == Route.TABLE
        draft = builder.build("Give the string functions in Hive in a table.", ev, route)
        formatted = SafePresentationEngine().format(draft, route, ConfidenceEngine().score(ev, True, 1.0, True))
        plain = formatted.plain_text()
        assert "| Item / Function | Description |" in plain
        assert "| length(str) | Returns string length |" in plain

    # 10. Citations are accurate and deduplicated
    def test_10_citations_are_accurate_and_deduplicated(self):
        engine = CitationEngine()
        ev1 = ValidatedEvidence(chunk_id="c1", document_id="doc1", page_id="5", text="Chunk 1 text", dense_score=0.8, bm25_score=0.8, fusion_score=0.8, validation_score=0.8, validation_passed=True)
        ev2 = ValidatedEvidence(chunk_id="c1", document_id="doc1", page_id="5", text="Chunk 1 text duplicate point", dense_score=0.8, bm25_score=0.8, fusion_score=0.8, validation_score=0.8, validation_passed=True)
        cites = engine.generate([ev1, ev2], {"doc1": "Hive_Notes (1).pdf"})
        assert len(cites) == 1
        assert "c1" in cites
        ref = cites["c1"].to_full_ref()
        assert ref == "[Source: Hive_Notes (1).pdf | Page 5]"

    # 11. Procedural 'how to' queries route to STEPS -> LIST
    def test_11_procedural_queries_route_to_steps(self):
        analyzer = QuestionAnalyzer()
        query = analyzer.analyze("How to deploy a machine learning model using Hive?")
        assert query.question_type == QuestionType.STEPS
        
        router = QuestionRouter()
        decision = router.route(query)
        assert decision.route == Route.LIST
        assert decision.answer_format == AnswerFormat.NUMBERED_LIST

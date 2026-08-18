"""
Question Router — Phase 7

Implements QuestionRouter interface (/docs/interfaces/QuestionRouter.md).

Routes a resolved query to one of:
  FACT_QA    → ExtractiveQA (span model)
  LIST       → Evidence sentence selection → bullets/numbered list
  SUMMARY    → Hierarchical sentence selection across sections
  TABLE      → Table extraction or parallel section comparison
  NO_ANSWER  → Insufficient or ambiguous evidence
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from core.question.analyzer import FormatHint, NormalizedQuery, QuestionType


class Route(str, Enum):
    FACT_QA  = "FACT_QA"
    LIST     = "LIST"
    SUMMARY  = "SUMMARY"
    TABLE    = "TABLE"
    NO_ANSWER = "NO_ANSWER"


class AnswerFormat(str, Enum):
    SHORT_FACT           = "SHORT_FACT"
    EXTRACTED_PARAGRAPH  = "EXTRACTED_PARAGRAPH"
    BULLET_LIST          = "BULLET_LIST"
    NUMBERED_LIST        = "NUMBERED_LIST"
    TABLE                = "TABLE"
    SECTION_SUMMARY      = "SECTION_SUMMARY"
    EXACT_QUOTE          = "EXACT_QUOTE"
    KEY_VALUE            = "KEY_VALUE"
    NO_ANSWER            = "NO_ANSWER"


@dataclass
class RetrievalPreview:
    """Lightweight signal from pre-retrieval probe."""
    has_table_evidence: bool = False
    evidence_count: int = 0
    top_score: float = 0.0


@dataclass
class RouteDecision:
    route: Route
    answer_format: AnswerFormat
    format_source: str          # "user_explicit" | "question_type" | "evidence_structure" | "fallback"
    confidence: float
    routing_reason: str

    def to_dict(self) -> dict:
        return {
            "route": self.route.value,
            "answer_format": self.answer_format.value,
            "format_source": self.format_source,
            "confidence": self.confidence,
            "routing_reason": self.routing_reason,
        }


MIN_SCORE_THRESHOLD = 0.15   # top score below this → NO_ANSWER


class QuestionRouter:
    """
    Deterministic routing engine. No ML. < 2 ms.

    Priority order:
    1. Ambiguity / no-evidence → NO_ANSWER
    2. User explicit format hint → override
    3. Question type signals
    4. Evidence structure signals
    5. Fallback → FACT_QA
    """

    def route(
        self,
        query: NormalizedQuery,
        preview: Optional[RetrievalPreview] = None,
    ) -> RouteDecision:

        preview = preview or RetrievalPreview()

        # ── Gate 0: Ambiguity ─────────────────────────────────────────────────
        if query.ambiguity_flag and query.resolution_confidence < 0.50:
            return RouteDecision(
                route=Route.FACT_QA,
                answer_format=AnswerFormat.SHORT_FACT,
                format_source="fallback",
                confidence=0.0,
                routing_reason="Query is ambiguous; defaulting to FACT_QA to let answerability engine decide.",
            )

        # ── Gate 1: Insufficient evidence (Removed) ───────────────────────────
        # The router no longer terminates on low preview score. Answerability is 
        # checked at the end of the pipeline.

        # ── Explicit user format hint (highest priority) ───────────────────────
        if query.format_hint == FormatHint.TABLE:
            return RouteDecision(
                route=Route.TABLE,
                answer_format=AnswerFormat.TABLE,
                format_source="user_explicit",
                confidence=0.95,
                routing_reason="User explicitly requested table format.",
            )

        if query.format_hint == FormatHint.BULLETS:
            return RouteDecision(
                route=Route.LIST, answer_format=AnswerFormat.BULLET_LIST,
                format_source="user_explicit", confidence=0.9,
                routing_reason="User requested bullet list.",
            )

        if query.format_hint == FormatHint.NUMBERED:
            return RouteDecision(
                route=Route.LIST, answer_format=AnswerFormat.NUMBERED_LIST,
                format_source="user_explicit", confidence=0.9,
                routing_reason="User requested numbered list.",
            )

        if query.format_hint == FormatHint.QUOTE:
            return RouteDecision(
                route=Route.FACT_QA, answer_format=AnswerFormat.EXACT_QUOTE,
                format_source="user_explicit", confidence=0.9,
                routing_reason="User requested exact quote.",
            )

        if query.format_hint == FormatHint.PARAGRAPH:
            return RouteDecision(
                route=Route.SUMMARY, answer_format=AnswerFormat.EXTRACTED_PARAGRAPH,
                format_source="user_explicit", confidence=0.9,
                routing_reason="User requested paragraph format.",
            )

        # ── Question type routing ──────────────────────────────────────────────
        qtype = query.question_type

        if qtype in (QuestionType.FACT, QuestionType.DEFINITION, QuestionType.NUMERICAL, QuestionType.QUOTE):
            return RouteDecision(
                route=Route.FACT_QA, answer_format=AnswerFormat.SHORT_FACT,
                format_source="question_type", confidence=0.85,
                routing_reason=f"Question type {qtype.value} → extractive span.",
            )

        if qtype in (QuestionType.LIST, QuestionType.STEPS):
            fmt = AnswerFormat.NUMBERED_LIST if qtype == QuestionType.STEPS else AnswerFormat.BULLET_LIST
            return RouteDecision(
                route=Route.LIST, answer_format=fmt,
                format_source="question_type", confidence=0.85,
                routing_reason=f"Question type {qtype.value} → list extraction.",
            )

        if qtype == QuestionType.COMPARISON:
            if preview.has_table_evidence:
                return RouteDecision(
                    route=Route.TABLE, answer_format=AnswerFormat.TABLE,
                    format_source="evidence_structure", confidence=0.80,
                    routing_reason="Comparison question + table evidence → table route.",
                )
            return RouteDecision(
                route=Route.LIST, answer_format=AnswerFormat.BULLET_LIST,
                format_source="question_type", confidence=0.75,
                routing_reason="Comparison question, no table evidence → side-by-side list.",
            )

        if qtype in (QuestionType.SUMMARY, QuestionType.EXPLANATION):
            return RouteDecision(
                route=Route.SUMMARY, answer_format=AnswerFormat.SECTION_SUMMARY,
                format_source="question_type", confidence=0.80,
                routing_reason=f"Question type {qtype.value} → hierarchical summary.",
            )

        # ── Fallback ──────────────────────────────────────────────────────────
        return RouteDecision(
            route=Route.FACT_QA, answer_format=AnswerFormat.SHORT_FACT,
            format_source="fallback", confidence=0.60,
            routing_reason="No specific route matched; defaulting to extractive QA.",
        )

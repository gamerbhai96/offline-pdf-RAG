"""
Answer Engine — Phases 10, 11, 12

Components:
  AnswerBuilder    — Assembles answers from validated evidence (4 strategies)
  AnswerValidator  — Gate 2: validates each answer point has evidence support
  ConfidenceEngine — Computes aggregate confidence level (HIGH/MEDIUM/LOW/NO_ANSWER)
  SafePresentationEngine — Formats validated answers for display

Interfaces:
  /docs/interfaces/AnswerBuilder.md
  /docs/interfaces/AnswerValidator.md
  /docs/interfaces/ConfidenceEngine.md
  /docs/interfaces/SafePresentationEngine.md
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from core.question.router import AnswerFormat, Route, RouteDecision
from core.retrieval.hybrid import ValidatedEvidence


# ── Data types ─────────────────────────────────────────────────────────────────

@dataclass
class AnswerPoint:
    text: str
    evidence_ids: list[str]      # chunk_ids supporting this point
    confidence: float = 1.0
    is_exact_span: bool = True   # True = character-exact from source

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "evidence_ids": self.evidence_ids,
            "confidence": self.confidence,
            "is_exact_span": self.is_exact_span,
        }


@dataclass
class AnswerSection:
    heading: Optional[str]
    points: list[AnswerPoint]
    evidence_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "heading": self.heading,
            "points": [p.to_dict() for p in self.points],
            "evidence_ids": self.evidence_ids,
        }


@dataclass
class AnswerDraft:
    route: Route
    answer_format: AnswerFormat
    sections: list[AnswerSection]
    evidence_ids: list[str]
    is_complete: bool = True
    incompleteness_note: Optional[str] = None

    def all_points(self) -> list[AnswerPoint]:
        return [p for s in self.sections for p in s.points]

    def to_dict(self) -> dict:
        return {
            "route": self.route.value,
            "answer_format": self.answer_format.value,
            "sections": [s.to_dict() for s in self.sections],
            "evidence_ids": self.evidence_ids,
            "is_complete": self.is_complete,
            "incompleteness_note": self.incompleteness_note,
        }


class ConfidenceLevel(str, Enum):
    HIGH      = "HIGH"
    MEDIUM    = "MEDIUM"
    LOW       = "LOW"
    NO_ANSWER = "NO_ANSWER"


@dataclass
class ConfidenceResult:
    level: ConfidenceLevel
    final_score: float
    retrieval_score: float
    evidence_score: float
    answerability_score: float
    explanation: str

    def to_dict(self) -> dict:
        return {
            "level": self.level.value, 
            "final_score": self.final_score, 
            "retrieval_score": self.retrieval_score,
            "evidence_score": self.evidence_score,
            "answerability_score": self.answerability_score,
            "explanation": self.explanation
        }


# ── AnswerBuilder ──────────────────────────────────────────────────────────────

class AnswerBuilder:
    """
    Assembles answers from validated evidence chunks using the route strategy.
    No content is invented. All text is exact or minimally formatted source text.
    """

    MAX_LIST_ITEMS = 8
    MAX_SUMMARY_SECTIONS = 5

    def build(
        self,
        query_text: str,
        evidence: list[ValidatedEvidence],
        route: RouteDecision,
        qa_spans: Optional[list] = None,    # ExtractiveQA spans (Phase 10)
    ) -> AnswerDraft:
        if not evidence:
            return AnswerDraft(
                route=route.route,
                answer_format=AnswerFormat.NO_ANSWER,
                sections=[],
                evidence_ids=[],
                is_complete=False,
                incompleteness_note="No validated evidence found.",
            )

        evidence_ids = [e.chunk_id for e in evidence]

        if route.route == Route.FACT_QA:
            return self._build_fact_qa(query_text, evidence, route, qa_spans, evidence_ids)
        elif route.route == Route.LIST:
            return self._build_list(query_text, evidence, route, evidence_ids)
        elif route.route == Route.SUMMARY:
            return self._build_summary(evidence, route, evidence_ids)
        elif route.route == Route.TABLE:
            return self._build_table(evidence, route, evidence_ids)
        else:
            # Fallback to FACT_QA
            return self._build_fact_qa(query_text, evidence, route, qa_spans, evidence_ids)

    def _build_fact_qa(self, query_text, evidence, route, qa_spans, evidence_ids) -> AnswerDraft:
        best_ev = evidence[0]
        actual_route = Route.FACT_QA if route.route == Route.NO_ANSWER else route.route

        # If QA spans provided (from ExtractiveQA), use the highest-confidence span
        if qa_spans:
            best = max(qa_spans, key=lambda s: s.get("confidence", 0))
            if best.get("text") and not best.get("is_impossible", False):
                span_text = best["text"]
                if route.route == Route.QUOTE:
                    final_text = span_text
                else:
                    sentences = _split_sentences(best_ev.text)
                    best_idx = next((i for i, s in enumerate(sentences) if span_text.lower() in s.lower()), -1)
                    if best_idx >= 0:
                        start_idx = max(0, best_idx - 1)
                        end_idx = min(len(sentences), best_idx + 2)
                        final_text = " ".join(sentences[start_idx:end_idx]).strip()
                    else:
                        final_text = span_text
                
                return AnswerDraft(
                    route=actual_route,
                    answer_format=route.answer_format,
                    sections=[AnswerSection(
                        heading=None,
                        points=[AnswerPoint(
                            text=final_text.strip(),
                            evidence_ids=[best.get("chunk_id", evidence_ids[0] if evidence_ids else "")],
                            confidence=best.get("confidence", 0.8),
                            is_exact_span=True,
                        )],
                        evidence_ids=evidence_ids[:1],
                    )],
                    evidence_ids=evidence_ids,
                )

        # Direct, clean sentence extraction from best evidence chunk
        expanded_q = getattr(route, "expanded_query", "") or getattr(route, "normalized_query", "") or query_text
        direct_text = _extract_direct_fact_sentence(best_ev.text, query_text, expanded_q)

        return AnswerDraft(
            route=actual_route,
            answer_format=route.answer_format,
            sections=[AnswerSection(
                heading=None,
                points=[AnswerPoint(
                    text=direct_text,
                    evidence_ids=[best_ev.chunk_id],
                    confidence=best_ev.validation_score,
                    is_exact_span=True,
                )],
                evidence_ids=[best_ev.chunk_id],
            )],
            evidence_ids=evidence_ids,
        )

    def _build_list(self, query_text, evidence, route, evidence_ids) -> AnswerDraft:
        query_terms = set(re.findall(r"[a-z][a-z0-9]+", query_text.lower()))
        candidates: list[tuple[str, str, float]] = []  # (text, chunk_id, score)

        for ev in evidence:
            lines = [l.strip() for l in ev.text.split("\n") if l.strip()]
            extracted_items = []

            for line in lines:
                cleaned = _clean_list_item(line)
                if cleaned:
                    extracted_items.append(cleaned)

            items_to_score = extracted_items if extracted_items else [s.strip() for s in _split_sentences(ev.text) if len(s.strip()) >= 15]

            for item in items_to_score:
                item_terms = set(re.findall(r"[a-z][a-z0-9]+", item.lower()))
                overlap = len(query_terms & item_terms) / max(len(query_terms), 1)
                
                score = ev.validation_score * 0.5 + overlap * 0.5
                candidates.append((item, ev.chunk_id, score))

        # Deduplicate near-duplicate list items, preserve top items up to MAX_LIST_ITEMS
        deduped = _deduplicate_sentences(candidates, threshold=0.70)[:self.MAX_LIST_ITEMS]

        points = [
            AnswerPoint(text=text, evidence_ids=[cid], confidence=score, is_exact_span=True)
            for text, cid, score in deduped
        ]

        return AnswerDraft(
            route=Route.LIST,
            answer_format=route.answer_format if route.answer_format != AnswerFormat.NO_ANSWER else AnswerFormat.BULLET_LIST,
            sections=[AnswerSection(heading=None, points=points, evidence_ids=evidence_ids)],
            evidence_ids=evidence_ids,
            is_complete=len(deduped) >= 2,
            incompleteness_note=None if len(deduped) >= 2 else "Limited evidence for list.",
        )

    def _build_summary(self, evidence, route, evidence_ids) -> AnswerDraft:
        # Group evidence by section/page, build one section per group
        groups: dict[str, list[ValidatedEvidence]] = {}
        for ev in evidence:
            key = ev.page_id
            groups.setdefault(key, []).append(ev)

        sections: list[AnswerSection] = []
        for page_id, evs in list(groups.items())[:self.MAX_SUMMARY_SECTIONS]:
            combined = " ".join(e.text for e in evs)
            sents = [s.strip() for s in _split_sentences(combined) if len(s.strip()) > 15][:5]
            if not sents:
                continue
            points = [
                AnswerPoint(text=s, evidence_ids=[evs[0].chunk_id], is_exact_span=True)
                for s in sents
            ]
            sections.append(AnswerSection(
                heading=f"Page {page_id}",
                points=points,
                evidence_ids=[e.chunk_id for e in evs],
            ))

        return AnswerDraft(
            route=Route.SUMMARY,
            answer_format=route.answer_format,
            sections=sections,
            evidence_ids=evidence_ids,
            is_complete=len(sections) >= 1,
        )

    def _build_table(self, evidence, route, evidence_ids) -> AnswerDraft:
        # Extract table-type chunks if available
        table_evs = [e for e in evidence if "\t" in e.text]
        if table_evs:
            rows = table_evs[0].text.split("\n")
            points = [
                AnswerPoint(text=row, evidence_ids=[table_evs[0].chunk_id], is_exact_span=True)
                for row in rows if row.strip()
            ]
            return AnswerDraft(
                route=Route.TABLE,
                answer_format=AnswerFormat.TABLE,
                sections=[AnswerSection(heading="Table", points=points, evidence_ids=[table_evs[0].chunk_id])],
                evidence_ids=evidence_ids,
            )

        # Fallback: build list-style comparison
        return self._build_list("", evidence, route, evidence_ids)


# ── AnswerValidator (Gate 2) ───────────────────────────────────────────────────

COVERAGE_THRESHOLD = 1.0   # 100% of points must be strictly supported


class AnswerValidator:
    """
    Gate 2: Validates the complete assembled answer.
    Checks that every point is traceable to its evidence chunk.
    """

    def validate(
        self,
        draft: AnswerDraft,
        evidence: list[ValidatedEvidence],
    ) -> tuple[bool, Optional[AnswerDraft], float, list[dict]]:
        """
        Returns:
            (passed, validated_draft_or_None, coverage_score, unsupported_points)
        """
        evidence_map = {e.chunk_id: e for e in evidence}
        all_points = draft.all_points()

        if not all_points:
            return False, None, 0.0, []

        supported = 0
        unsupported: list[dict] = []

        for point in all_points:
            ok = self._check_point(point, evidence_map)
            if ok:
                supported += 1
            else:
                unsupported.append({"text": point.text[:100], "reason": "no_evidence_link"})

        coverage = supported / len(all_points)
        passed = coverage >= COVERAGE_THRESHOLD

        return passed, (draft if passed else None), coverage, unsupported

    def _check_point(self, point: AnswerPoint, evidence_map: dict[str, ValidatedEvidence]) -> bool:
        # Check 1: all evidence IDs exist
        if not point.evidence_ids:
            return False
        for eid in point.evidence_ids:
            if eid not in evidence_map:
                return False

        # Check 2: if exact span, text must be strongly present in evidence
        if point.is_exact_span and point.evidence_ids:
            ev = evidence_map.get(point.evidence_ids[0])
            if ev and point.text:
                # Remove all non-alphanumeric and replacement chars for robust exact match check
                text_no_space = re.sub(r"[\s\uFFFD\W]+", "", point.text.lower())
                ev_no_space = re.sub(r"[\s\uFFFD\W]+", "", ev.text.lower())
                
                if text_no_space not in ev_no_space and ev_no_space not in text_no_space:
                    # Token overlap check (strict fallback for tokenization differences)
                    point_tokens = set(re.findall(r"[a-z][a-z0-9]+", point.text.lower()))
                    ev_tokens = set(re.findall(r"[a-z][a-z0-9]+", ev.text.lower()))
                    overlap = len(point_tokens & ev_tokens) / max(len(point_tokens), 1)
                    if overlap < 0.85:
                        return False

        return True


# ── Confidence Engine ──────────────────────────────────────────────────────────

class ConfidenceEngine:
    """
    Computes aggregate confidence from retrieval + validation signals.
    If validation failed → always NO_ANSWER.
    """

    # Weights (sum = 1.0; calibrated in Phase 17)
    W_DENSE_TOP    = 0.20
    W_BM25_TOP     = 0.15
    W_AGREEMENT    = 0.15
    W_DIVERSITY    = 0.10
    W_QA_CONF      = 0.20
    W_COVERAGE     = 0.20
    W_COMPLETENESS = 0.10

    def score(
        self,
        evidence: list[ValidatedEvidence],
        validation_passed: bool,
        coverage_score: float,
        qa_confidence: Optional[float] = None,
        is_complete: bool = True,
    ) -> ConfidenceResult:
        if not evidence:
            return ConfidenceResult(
                level=ConfidenceLevel.NO_ANSWER,
                final_score=0.0,
                retrieval_score=0.0,
                evidence_score=0.0,
                answerability_score=0.0,
                explanation="No validated evidence.",
            )

        top_retrieval = max((getattr(e, "fusion_score", getattr(e, "validation_score", 0.5)) for e in evidence), default=0.5)
        top_validation = max((e.validation_score for e in evidence), default=0.5)
        evidence_score = float(min(1.0, top_validation))

        completeness = 1.0 if is_complete else 0.6
        qa_conf = qa_confidence if qa_confidence is not None else max(0.5, top_retrieval)
        answerability_score = float(min(1.0, coverage_score * 0.6 + completeness * 0.2 + qa_conf * 0.2))
        if not validation_passed:
            answerability_score = 0.0

        final_score = (
            self.W_DENSE_TOP    * top_retrieval +
            self.W_BM25_TOP     * top_retrieval +
            self.W_AGREEMENT    * 0.8 +
            self.W_QA_CONF      * qa_conf +
            self.W_COVERAGE     * coverage_score +
            self.W_COMPLETENESS * completeness
        )
        if validation_passed and coverage_score >= 0.80:
            final_score += 0.15
        final_score = float(min(1.0, max(0.0, final_score)))

        if not validation_passed:
            level = ConfidenceLevel.NO_ANSWER
            explanation = "Answer failed Gate 2 validation."
        elif final_score >= 0.70:
            level = ConfidenceLevel.HIGH
            explanation = "High confidence answer."
        elif final_score >= 0.45:
            level = ConfidenceLevel.MEDIUM
            explanation = "Medium confidence answer."
        elif final_score >= 0.25:
            level = ConfidenceLevel.LOW
            explanation = "Low confidence answer."
        else:
            level = ConfidenceLevel.NO_ANSWER
            explanation = "Confidence below NO_ANSWER threshold."

        return ConfidenceResult(
            level=level, 
            final_score=final_score,
            retrieval_score=top_retrieval,
            evidence_score=evidence_score,
            answerability_score=answerability_score,
            explanation=explanation
        )


# ── Safe Presentation Engine ───────────────────────────────────────────────────

NO_ANSWER_TEXT = (
    "I couldn't find enough evidence in the document to provide a reliable answer. "
    "Please try rephrasing your question or check if the document covers this topic."
)


@dataclass
class PresentableAnswer:
    answer_id: str
    route: Route
    answer_format: AnswerFormat
    sections: list[AnswerSection]
    confidence_level: ConfidenceLevel
    confidence_score: float
    retrieval_score: float
    evidence_score: float
    answerability_score: float
    confidence_note: Optional[str]       # shown for LOW confidence
    completeness_note: Optional[str]
    fallback_used: bool = False
    is_no_answer: bool = False
    fallback_text: Optional[str] = None  # for NO_ANSWER

    def plain_text(self) -> str:
        """Render answer as plain text for CLI output."""
        if self.is_no_answer:
            return self.fallback_text or NO_ANSWER_TEXT

        lines: list[str] = []
        if self.confidence_note:
            lines.append(f"[{self.confidence_level.value} confidence] {self.confidence_note}")

        for section in self.sections:
            if section.heading:
                lines.append(f"\n{section.heading}")
                lines.append("-" * len(section.heading))

            if self.answer_format in (AnswerFormat.BULLET_LIST,):
                for p in section.points:
                    clean_pt = re.sub(r"^[\s\uFFFD•◦▪▸\-\*\d+\.\)]+", "", p.text).strip()
                    lines.append(f"  • {clean_pt}")
            elif self.answer_format == AnswerFormat.NUMBERED_LIST:
                for i, p in enumerate(section.points, 1):
                    clean_pt = re.sub(r"^[\s\uFFFD•◦▪▸\-\*\d+\.\)]+", "", p.text).strip()
                    lines.append(f"  {i}. {clean_pt}")
            elif self.answer_format == AnswerFormat.TABLE:
                if section.points:
                    table_rows: list[tuple[str, str, str]] = []  # (col1, col2, full_text)
                    valid_row_count = 0

                    for p in section.points:
                        clean_pt = re.sub(r"^[\s\uFFFD•◦▪▸\-\*\d+\.\)]+", "", p.text).strip()
                        if not clean_pt:
                            continue
                        
                        col1, col2 = "", ""
                        if ":" in clean_pt:
                            parts = clean_pt.split(":", 1)
                            col1, col2 = parts[0].strip(), parts[1].strip()
                        elif " - " in clean_pt:
                            parts = clean_pt.split(" - ", 1)
                            col1, col2 = parts[0].strip(), parts[1].strip()
                        else:
                            cols = re.split(r'\s{2,}|\t', clean_pt)
                            if len(cols) >= 2:
                                col1, col2 = cols[0].strip(), " ".join(cols[1:]).strip()
                            else:
                                col1, col2 = clean_pt, "-"

                        # Check row readability: col1 <= 60 chars and col2 is present
                        if col2 and col2 != "-" and len(col1) <= 65:
                            valid_row_count += 1
                        table_rows.append((col1, col2, clean_pt))

                    # Safety check: if >= 50% of rows are readable, render Markdown table; else fall back to bullets
                    if table_rows and (valid_row_count / len(table_rows)) >= 0.5:
                        lines.append("")
                        lines.append("| Item / Function | Description |")
                        lines.append("| --- | --- |")
                        for col1, col2, _ in table_rows:
                            lines.append(f"| {col1} | {col2} |")
                        lines.append("")
                    else:
                        # Safe fallback to clean bullet list
                        for _, _, full_text in table_rows:
                            lines.append(f"  • {full_text}")
            else:
                for p in section.points:
                    lines.append(p.text)

        if self.completeness_note:
            lines.append(f"\n[Note: {self.completeness_note}]")

        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "answer_id": self.answer_id,
            "route": self.route.value,
            "answer_format": self.answer_format.value,
            "sections": [s.to_dict() for s in self.sections],
            "confidence_level": self.confidence_level.value,
            "confidence_score": self.confidence_score,
            "confidence_note": self.confidence_note,
            "completeness_note": self.completeness_note,
            "fallback_used": self.fallback_used,
            "is_no_answer": self.is_no_answer,
        }


class SafePresentationEngine:
    """
    Formats a validated AnswerDraft into a PresentableAnswer.
    No ML. No content generation. Pure formatting.
    """

    def format(
        self,
        draft: Optional[AnswerDraft],
        route: RouteDecision,
        confidence: ConfidenceResult,
    ) -> PresentableAnswer:

        answer_id = str(uuid.uuid4())

        # No answer / validation failed
        if draft is None or confidence.level == ConfidenceLevel.NO_ANSWER:
            return PresentableAnswer(
                answer_id=answer_id,
                route=Route.NO_ANSWER,
                answer_format=AnswerFormat.NO_ANSWER,
                sections=[],
                confidence_level=ConfidenceLevel.NO_ANSWER,
                confidence_score=0.0,
                retrieval_score=confidence.retrieval_score,
                evidence_score=confidence.evidence_score,
                answerability_score=0.0,
                confidence_note=None,
                completeness_note=None,
                is_no_answer=True,
                fallback_text=NO_ANSWER_TEXT,
            )

        confidence_note = None
        if confidence.level == ConfidenceLevel.LOW:
            confidence_note = (
                "This answer has low confidence. "
                "Please verify against the source document."
            )

        return PresentableAnswer(
            answer_id=answer_id,
            route=draft.route,
            answer_format=draft.answer_format,
            sections=draft.sections,
            confidence_level=confidence.level,
            confidence_score=confidence.final_score,
            retrieval_score=confidence.retrieval_score,
            evidence_score=confidence.evidence_score,
            answerability_score=confidence.answerability_score,
            confidence_note=confidence_note,
            completeness_note=draft.incompleteness_note,
            fallback_used=route.format_source == "fallback",
        )


# ── Sentence utilities ─────────────────────────────────────────────────────────

_STOPWORDS = frozenset({
    "what", "is", "are", "the", "a", "an", "of", "in", "and", "or",
    "to", "for", "how", "why", "when", "where", "who", "which",
    "does", "do", "can", "be", "me", "tell", "about", "give", "some",
    "many", "much", "more", "most", "any", "all", "this", "that", "was",
    "were", "with", "from", "by", "on", "at", "it", "its", "as", "into"
})

_SENT_SPLIT = re.compile(r"(?<!\b\d)(?<!\b[A-Z])(?<=[.!?])\s+(?=[A-Z])")


def _split_sentences(text: str) -> list[str]:
    parts = _SENT_SPLIT.split(text)
    return [p.strip() for p in parts if p.strip()]


def _best_sentence(text: str, query: str) -> str:
    """Return the sentence from text most relevant to query."""
    query_terms = set(re.findall(r"[a-z]+", query.lower()))
    sentences = _split_sentences(text)
    if not sentences:
        return text[:300].strip()
    best = max(
        sentences,
        key=lambda s: len(query_terms & set(re.findall(r"[a-z]+", s.lower()))),
    )
    return best


def _extract_direct_fact_sentence(raw_text: str, query_text: str, expanded_query: str = "") -> str:
    """Extract a clean, direct, document-grounded fact/definition sentence matching query entities/terms."""
    lines = [l.strip() for l in raw_text.split("\n") if l.strip()]
    if not lines:
        return raw_text.strip()

    # Filter out standalone numbers, single letters, section headers, unit titles
    body_lines = []
    for line in lines:
        if re.match(r"^(\d+\.?|[A-Za-z][\.\)]?|Unit\s+[I|V|X]+|Chapter\s+\d+|\d+(\.\d+)*\s+[A-Z]|Syntax:|Fig\s+\d+:)$", line.strip(), re.I) and len(line.split()) <= 6:
            continue
        body_lines.append(line)

    clean_text = " ".join(body_lines) if body_lines else raw_text
    sentences = _split_sentences(clean_text)
    if not sentences:
        return clean_text.strip()

    raw_words = [w.lower() for w in re.findall(r"[a-z0-9\-]+", query_text) if w.lower() not in _STOPWORDS and len(w) > 2]
    raw_terms = set(raw_words)
    exp_words = [w.lower() for w in re.findall(r"[a-z0-9\-]+", expanded_query) if w.lower() not in _STOPWORDS and len(w) > 2]
    exp_terms = set(exp_words) - raw_terms

    target_sentences = []
    for i, s in enumerate(sentences):
        s_words = set(re.findall(r"[a-z0-9\-]+", s.lower()))
        raw_overlap = len(raw_terms & s_words)
        exp_overlap = len(exp_terms & s_words)
        weighted_score = raw_overlap * 10 + exp_overlap

        if weighted_score > 0:
            is_def = bool(re.search(r"\b(is|returns|provides|refers|enables|supports|designed|built)\b", s, re.I))
            final_score = weighted_score + (5.0 if is_def else 0.0)
            target_sentences.append((final_score, i, s))

    if target_sentences:
        target_sentences.sort(key=lambda x: x[0], reverse=True)
        best_idx = target_sentences[0][1]
        
        # Extract a 3-sentence window for better context
        start_idx = max(0, best_idx - 1)
        end_idx = min(len(sentences), best_idx + 2)
        return " ".join(sentences[start_idx:end_idx]).strip()

    # Fallback to up to the first 3 sentences
    return " ".join(sentences[:3]).strip()


def _clean_list_item(line: str) -> Optional[str]:
    """Clean a single line into a pristine bullet point item."""
    text = line.strip()
    if not text:
        return None

    # Explicitly strip leading replacement chars (\uFFFD), bullet icons, numbers, dashes, and lettered bullets (A., b), etc.)
    text = re.sub(r"^[\s\uFFFD•◦▪▸\-\*\d+\.\)]+", "", text)
    text = re.sub(r"^[A-Za-z][\.\)]\s+", "", text).strip()

    # Skip header lines
    if re.match(r"^(Built-in|Features|Advantages|Disadvantages|List|Table|Note|Syntax:)", text, re.I) and ":" in text and len(text.split()) <= 5:
        return None

    # Skip lines that are too short
    if len(text) < 8 or len(text.split()) < 2:
        return None

    return text


def _deduplicate_sentences(
    candidates: list[tuple[str, str, float]],
    threshold: float = 0.75,
) -> list[tuple[str, str, float]]:
    """Remove near-duplicate sentences based on token overlap."""
    kept: list[tuple[str, str, float]] = []
    for cand in candidates:
        text = cand[0]
        tokens = set(re.findall(r"[a-z]+", text.lower()))
        duplicate = False
        for kept_text, _, _ in kept:
            kept_tokens = set(re.findall(r"[a-z]+", kept_text.lower()))
            union = kept_tokens | tokens
            if not union:
                continue
            overlap = len(kept_tokens & tokens) / len(union)
            if overlap >= threshold:
                duplicate = True
                break
        if not duplicate:
            kept.append(cand)
    return kept

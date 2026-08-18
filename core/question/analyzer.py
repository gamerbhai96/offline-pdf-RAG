"""
Question Understanding — Phase 6

Implements:
  QuestionAnalyzer  (/docs/interfaces/QuestionAnalyzer.md)
  ConversationResolver (/docs/interfaces/ConversationResolver.md)

All rule-based. No ML model required.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ── Enumerations ───────────────────────────────────────────────────────────────

class QuestionType(str, Enum):
    FACT = "FACT"
    DEFINITION = "DEFINITION"
    EXPLANATION = "EXPLANATION"
    LIST = "LIST"
    STEPS = "STEPS"
    COMPARISON = "COMPARISON"
    SUMMARY = "SUMMARY"
    QUOTE = "QUOTE"
    NUMERICAL = "NUMERICAL"
    TABLE = "TABLE"
    UNKNOWN = "UNKNOWN"


class FormatHint(str, Enum):
    BULLETS = "BULLETS"
    NUMBERED = "NUMBERED"
    TABLE = "TABLE"
    PARAGRAPH = "PARAGRAPH"
    QUOTE = "QUOTE"


# ── Data types ─────────────────────────────────────────────────────────────────

@dataclass
class Entity:
    text: str
    etype: str        # PERSON | ORG | TECH | DATE | NUMBER | ABBREV | OTHER
    start: int
    end: int


@dataclass
class NormalizedQuery:
    raw_query: str
    normalized_query: str
    resolved_query: str
    expanded_query: str = ""
    entities: list[Entity] = field(default_factory=list)
    target_attribute: Optional[str] = None
    question_type: QuestionType = QuestionType.UNKNOWN
    format_hint: Optional[FormatHint] = None
    document_scope: str = "ALL"
    important_terms: list[str] = field(default_factory=list)
    coreference_resolved: bool = False
    resolution_confidence: float = 1.0
    ambiguity_flag: bool = False
    ambiguity_reason: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "raw_query": self.raw_query,
            "normalized_query": self.normalized_query,
            "resolved_query": self.resolved_query,
            "expanded_query": self.expanded_query,
            "question_type": self.question_type.value,
            "format_hint": self.format_hint.value if self.format_hint else None,
            "document_scope": self.document_scope,
            "target_attribute": self.target_attribute,
            "ambiguity_flag": self.ambiguity_flag,
            "ambiguity_reason": self.ambiguity_reason,
            "coreference_resolved": self.coreference_resolved,
            "resolution_confidence": self.resolution_confidence,
        }


@dataclass
class ConversationContext:
    session_id: str
    turn_index: int = 0
    resolved_topics: list[str] = field(default_factory=list)
    entity_stack: list[dict] = field(default_factory=list)  # {text, type, turn, mention_count}
    last_document_scope: Optional[str] = None
    last_format_preference: Optional[str] = None

    MAX_STACK_TURNS = 10   # Prune entities older than this many turns

    def push_entities(self, entities: list[Entity], turn: int) -> None:
        """Add entities from the current turn to the entity stack."""
        for e in entities:
            # Update existing or append
            found = False
            for entry in self.entity_stack:
                if entry["text"].lower() == e.text.lower():
                    entry["turn"] = turn
                    entry["mention_count"] += 1
                    found = True
                    break
            if not found:
                self.entity_stack.append({
                    "text": e.text, "type": e.etype,
                    "turn": turn, "mention_count": 1,
                })
        # Prune stale entities
        self.entity_stack = [
            e for e in self.entity_stack
            if (turn - e["turn"]) <= self.MAX_STACK_TURNS
        ]

    def latest_entity(self, plural: bool = False) -> Optional[str]:
        """Return the most recently mentioned entity."""
        if not self.entity_stack:
            return None
        return self.entity_stack[-1]["text"]

    def latest_topic(self) -> Optional[str]:
        """Return the most recently resolved topic."""
        return self.resolved_topics[-1] if self.resolved_topics else None


# ── Question type detection patterns ──────────────────────────────────────────

_FORMAT_PATTERNS: list[tuple[re.Pattern, FormatHint]] = [
    (re.compile(r"\b(in bullet|as bullet|in points|as points|bullet points)\b", re.I), FormatHint.BULLETS),
    (re.compile(r"\bin (a )?numbered list\b", re.I), FormatHint.NUMBERED),
    (re.compile(r"\b(in a table|as a table|in tabular|in table form|tabulate|as table)\b", re.I), FormatHint.TABLE),
    (re.compile(r"\bin (a |one )?paragraph\b", re.I), FormatHint.PARAGRAPH),
    (re.compile(r"\b(exact (quote|words)|verbatim|quote)\b", re.I), FormatHint.QUOTE),
]

_TYPE_PATTERNS: list[tuple[re.Pattern, QuestionType]] = [
    # High-specificity patterns first
    (re.compile(r"\b(compare|difference between|distinguish|contrast|vs\.?|versus)\b", re.I), QuestionType.COMPARISON),
    (re.compile(r"\b(steps to|step by step|how to|how (do i|do we|can i|can we)|configure|install|deploy|setup)\b", re.I), QuestionType.STEPS),
    (re.compile(r"\b(summarize|summary|overview|in brief|what does .* cover|what is .* about)\b", re.I), QuestionType.SUMMARY),
    (re.compile(r"\b(exact (quote|words)|verbatim|did .* say|what exactly)\b", re.I), QuestionType.QUOTE),
    (re.compile(r"\b(how many|how much|how long|when was|what year|how often|what (number|count|percentage|rate|speed|size|age))\b", re.I), QuestionType.NUMERICAL),
    (re.compile(r"^(list|enumerate|what are all|what are the (different )?|give me a list|name all)\b", re.I), QuestionType.LIST),
    (re.compile(r"\b(advantages|disadvantages|benefits|drawbacks|pros|cons|features|types|types of|functions|methods|systems|components)\b", re.I), QuestionType.LIST),
    (re.compile(r"^(explain|why|how does|what causes|what makes|how was)\b", re.I), QuestionType.EXPLANATION),
    # DEFINITION: what is X / define X / meaning of X / how was X
    (re.compile(r"^(what is|what are|define|definition of|meaning of|how was|how did)\b", re.I), QuestionType.DEFINITION),
    (re.compile(r"^(what|who|where|when|which)\b", re.I), QuestionType.FACT),
]

_AMBIGUOUS_TRIGGERS = re.compile(
    r"^\s*(what are the steps|what (are they|is it)|how|why|when|where)\s*[\?\.]\s*$", re.I
)

_PRONOUN_SINGULAR = re.compile(r"\b(it|its|this|that)\b", re.I)
_PRONOUN_PLURAL   = re.compile(r"\b(they|them|their|these|those)\b", re.I)
_PRONOUN_PERSON   = re.compile(r"\b(he|she|him|her|his|hers)\b", re.I)


# ── QuestionAnalyzer ───────────────────────────────────────────────────────────

class QuestionAnalyzer:
    """
    Analyzes raw queries into NormalizedQuery.
    Rule-based. No ML. Fast (<10ms on any device).
    """

    def analyze(
        self,
        raw_query: str,
        context: Optional[ConversationContext] = None,
    ) -> NormalizedQuery:
        if not raw_query or not raw_query.strip():
            return NormalizedQuery(
                raw_query=raw_query,
                normalized_query="",
                resolved_query="",
                ambiguity_flag=True,
                ambiguity_reason="Empty query",
            )

        norm = _normalize(raw_query)
        qtype = self._detect_type(norm)
        fmt = self._detect_format(raw_query)
        entities = _extract_entities(raw_query, norm)
        important_terms = _extract_important_terms(norm, entities)
        target_attribute = _extract_target_attribute(norm, qtype)
        expanded_query = _expand_query(norm, qtype, target_attribute, entities)

        # Detect ambiguity: very short or has only pronouns with no context
        ambiguous, reason = self._check_ambiguity(norm, context)

        return NormalizedQuery(
            raw_query=raw_query,
            normalized_query=norm,
            resolved_query=norm,           # resolver will update this
            expanded_query=expanded_query, # intent-based synonym expansion
            entities=entities,
            target_attribute=target_attribute,
            question_type=qtype,
            format_hint=fmt,
            document_scope=context.last_document_scope or "ALL" if context else "ALL",
            important_terms=important_terms,
            ambiguity_flag=ambiguous,
            ambiguity_reason=reason,
        )

    def _detect_type(self, text: str) -> QuestionType:
        for pattern, qtype in _TYPE_PATTERNS:
            if pattern.search(text):
                return qtype
        return QuestionType.UNKNOWN

    def _detect_format(self, text: str) -> Optional[FormatHint]:
        for pattern, hint in _FORMAT_PATTERNS:
            if pattern.search(text):
                return hint
        return None

    def _check_ambiguity(
        self, norm: str, context: Optional[ConversationContext]
    ) -> tuple[bool, Optional[str]]:
        if _AMBIGUOUS_TRIGGERS.match(norm):
            return True, "Query is too vague — please provide more context."
        has_pronoun = bool(
            _PRONOUN_SINGULAR.search(norm) or
            _PRONOUN_PLURAL.search(norm) or
            _PRONOUN_PERSON.search(norm)
        )
        if has_pronoun and (not context or not context.entity_stack):
            return True, "Query contains pronouns but no prior context exists."
        return False, None


# ── ConversationResolver ───────────────────────────────────────────────────────

class ConversationResolver:
    """
    Resolves pronoun and ellipsis references using entity stack.
    Rule-based. Confidence-gated: low confidence → ambiguity_flag.
    """

    HIGH_CONF = 0.85
    MED_CONF  = 0.60
    LOW_CONF  = 0.40

    def resolve(
        self,
        query: NormalizedQuery,
        context: Optional[ConversationContext],
    ) -> NormalizedQuery:
        """
        Attempt to resolve references in query.resolved_query.
        Returns a new NormalizedQuery with resolved_query updated.
        """
        if not context:
            return query   # first turn — nothing to resolve

        text = query.normalized_query
        resolved = text
        confidence = 1.0
        substituted = False

        # Singular pronoun resolution
        if _PRONOUN_SINGULAR.search(text):
            topic = context.latest_entity() or context.latest_topic()
            if topic:
                resolved = _PRONOUN_SINGULAR.sub(topic, resolved, count=1)
                confidence = self.HIGH_CONF
                substituted = True
            else:
                confidence = self.LOW_CONF

        # Plural pronoun resolution
        if _PRONOUN_PLURAL.search(text):
            topic = context.latest_entity() or context.latest_topic()
            if topic:
                resolved = _PRONOUN_PLURAL.sub(topic, resolved, count=1)
                confidence = min(confidence, self.MED_CONF)
                substituted = True
            else:
                confidence = self.LOW_CONF

        ambiguous = confidence < self.MED_CONF
        reason = "Cannot confidently resolve reference — please clarify." if ambiguous else None

        return NormalizedQuery(
            raw_query=query.raw_query,
            normalized_query=query.normalized_query,
            resolved_query=resolved,
            expanded_query=query.expanded_query,
            entities=query.entities,
            target_attribute=query.target_attribute,
            question_type=query.question_type,
            format_hint=query.format_hint,
            document_scope=query.document_scope,
            important_terms=query.important_terms,
            coreference_resolved=substituted,
            resolution_confidence=confidence,
            ambiguity_flag=ambiguous or query.ambiguity_flag,
            ambiguity_reason=reason or query.ambiguity_reason,
        )


# ── Helper functions ───────────────────────────────────────────────────────────

def _normalize(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s\?\.\!\,\-]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text


_ABBREV_PATTERN = re.compile(r"\b[A-Z]{2,}\b")
_NUMBER_PATTERN = re.compile(r"\b\d+(\.\d+)?\b")
_TECH_WORDS = frozenset({
    "tcp", "udp", "http", "https", "api", "sql", "ram", "cpu", "gpu",
    "hnsw", "bm25", "onnx", "bert", "gpt", "llm", "rag", "pdf", "ocr",
    "hadoop", "spark", "kafka", "redis", "docker", "kubernetes",
})


_ATTRIBUTE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(string functions?|built-in string functions?)\b", re.I), "string functions"),
    (re.compile(r"\b(math|mathematical) functions?\b", re.I), "mathematical functions"),
    (re.compile(r"\b(aggregate|aggregation) functions?\b", re.I), "aggregate functions"),
    (re.compile(r"\b(functions?)\b", re.I), "functions"),
    (re.compile(r"\b(hive layer|layer|components|architecture)\b", re.I), "architecture"),
    (re.compile(r"\b(features?|capabilities|characteristics)\b", re.I), "features"),
    (re.compile(r"\b(advantages?|benefits?|pros)\b", re.I), "advantages"),
    (re.compile(r"\b(disadvantages?|drawbacks?|limitations?|cons)\b", re.I), "disadvantages"),
    (re.compile(r"\b(introduced?|introduction|history|origin)\b", re.I), "introduction"),
    (re.compile(r"\b(metastore|data model|file formats?)\b", re.I), "metastore"),
    (re.compile(r"\b(difference|comparison|vs\.?|versus)\b", re.I), "comparison"),
    (re.compile(r"\b(definition|meaning|what is|what are|define)\b", re.I), "definition"),
]


def _extract_target_attribute(norm_query: str, qtype: QuestionType = QuestionType.UNKNOWN) -> Optional[str]:
    """Extract a target attribute (e.g., 'definition', 'features', 'string functions', 'architecture')."""
    for pattern, attr in _ATTRIBUTE_PATTERNS:
        if pattern.search(norm_query):
            if attr == "definition":
                # Don't use definition if a more specific attribute pattern matched
                specific_match = any(
                    p.search(norm_query) for p, a in _ATTRIBUTE_PATTERNS if a != "definition"
                )
                if specific_match:
                    continue
            return attr
            
    if qtype in (QuestionType.DEFINITION, QuestionType.FACT):
        return "definition"
        
    if len(norm_query.split()) <= 2:
        return "definition"

    return None


_STOPWORDS = frozenset({
    "what", "is", "are", "the", "a", "an", "of", "in", "and", "or",
    "to", "for", "how", "why", "when", "where", "who", "which",
    "does", "do", "can", "be", "me", "tell", "about", "give", "some",
    "many", "much", "more", "most", "any", "all", "this", "that", "was",
    "were", "with", "from", "by", "on", "at", "it", "its", "as", "into"
})


def _extract_important_terms(text: str, entities: list[Entity]) -> list[str]:
    """Extract important non-stop terms beyond detected entities."""
    entity_texts = {e.text.lower() for e in entities}
    terms = []
    for word in re.findall(r"[a-z][a-z0-9\-]+", text):
        if word not in _STOPWORDS and word not in entity_texts and len(word) > 3:
            terms.append(word)
    tech = [t for t in terms if t in _TECH_WORDS]
    other = [t for t in terms if t not in _TECH_WORDS]
    return tech + other[:5]


def _extract_entities(raw_query: str, norm_query: str) -> list[Entity]:
    """Extract clean domain entities, excluding question stopwords."""
    entities: list[Entity] = []
    seen: set[str] = set()

    # 1. Uppercase acronyms from raw_query (e.g., HQL, HDFS, ETL, SQL, CLI, TCP)
    for m in re.finditer(r"\b[A-Z]{2,}\b", raw_query):
        word = m.group()
        if word.lower() not in _STOPWORDS and word.lower() not in seen:
            entities.append(Entity(text=word, etype="ABBREV", start=m.start(), end=m.end()))
            seen.add(word.lower())

    # 2. Capitalized words from raw_query (e.g., Apache, Hive)
    for m in re.finditer(r"\b[A-Z][a-z0-9]+\b", raw_query):
        word = m.group()
        if word.lower() not in _STOPWORDS and word.lower() not in seen:
            entities.append(Entity(text=word, etype="TECH", start=m.start(), end=m.end()))
            seen.add(word.lower())

    # 3. Known domain entities in norm_query
    _DOMAIN_ENTITIES = {
        "hive", "hql", "hiveql", "hdfs", "hadoop", "mapreduce", "bucketing", "partitioning",
        "metastore", "tez", "spark", "pig", "sqoop", "flume", "zookeeper", "hbase",
        "tcp", "udp", "http", "https", "sql", "table", "database"
    }

    words = re.findall(r"\b[a-z0-9\-]+\b", norm_query)
    for w in words:
        if w in _DOMAIN_ENTITIES and w not in seen:
            display_text = w.upper() if len(w) <= 4 else w.capitalize()
            entities.append(Entity(text=display_text, etype="TECH", start=0, end=len(w)))
            seen.add(w)

    # 4. Fallback: non-stopword terms
    if not entities:
        for w in words:
            if w not in _STOPWORDS and len(w) > 2 and w not in seen:
                entities.append(Entity(text=w.capitalize(), etype="OTHER", start=0, end=len(w)))
                seen.add(w)

    return entities


def _expand_query(
    text: str,
    qtype: QuestionType,
    target_attribute: Optional[str],
    entities: list[Entity] = None,
) -> str:
    """
    Controlled intent-, entity-, and attribute-aware query expansion.
    The original query is ALWAYS retained at the beginning.
    """
    expansions: list[str] = []
    text_lower = text.lower()
    entity_texts = {e.text.lower() for e in (entities or [])}

    # 1. Entity-based controlled expansion
    if "hql" in entity_texts or "hql" in text_lower or "hiveql" in text_lower:
        expansions.append("Hive Query Language HiveQL HQL")
    if "bucketing" in entity_texts or "bucketing" in text_lower or "bucket" in text_lower:
        expansions.append("Hive bucketing bucket clustered by CLUSTERED BY INTO BUCKETS")
    if "partitioning" in entity_texts or "partitioning" in text_lower or "partition" in text_lower:
        expansions.append("partition partitioned by PARTITIONED BY sub-directories")
    if "metastore" in entity_texts or "metastore" in text_lower:
        expansions.append("metastore service local remote embedded metastore DB")

    # 2. Attribute-based controlled expansion
    if target_attribute == "features":
        expansions.append("features characteristics capabilities")
    elif target_attribute in ("string functions", "functions") and ("string" in text_lower or "functions" in text_lower):
        expansions.append("string functions built-in string functions string manipulation functions length reverse concat substr upper lower trim")
    elif target_attribute in ("advantages", "benefits", "pros"):
        expansions.append("advantages benefits pros characteristics positive features capabilities")
    elif target_attribute in ("disadvantages", "drawbacks", "limitations", "cons"):
        expansions.append("disadvantages drawbacks limitations cons negative")
    elif target_attribute == "introduction":
        expansions.append("introduction introduced origin background history")
    elif target_attribute == "architecture":
        expansions.append("HIVE LAYER CLI Command Line Interface Driver Compiler Optimizer Executor Metastore")
    elif target_attribute == "definition":
        expansions.append("definition define overview meaning")

    # 3. Intent-based controlled expansion
    if qtype == QuestionType.STEPS:
        expansions.append("steps sequence instructions process algorithm")
    elif qtype == QuestionType.COMPARISON:
        expansions.append("difference comparison versus contrast side by side")
    elif qtype == QuestionType.SUMMARY:
        expansions.append("overview summary introduction abstract")

    if expansions:
        # Deduplicate terms while retaining order
        new_words = []
        seen = set(text_lower.split())
        for exp in expansions:
            for w in exp.split():
                if w.lower() not in seen:
                    new_words.append(w)
                    seen.add(w.lower())
        if new_words:
            return text + " " + " ".join(new_words)

    return text

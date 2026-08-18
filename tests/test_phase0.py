"""
Phase 0 tests — Harness integrity, schema validation, interface completeness.

These tests verify the evaluation framework itself is correct
before any intelligence components are built.

Run:
    pytest tests/test_phase0.py -v
"""

import json
from pathlib import Path
import pytest

ROOT = Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# Schema presence tests
# ---------------------------------------------------------------------------

REQUIRED_SCHEMAS = [
    "Document.json",
    "Page.json",
    "Section.json",
    "Chunk.json",
    "IndexMetadata.json",
    "Evidence.json",
    "NormalizedQuery.json",
    "ConversationContext.json",
    "Answer.json",
    "Citation.json",
    "ModelRegistry.json",
]

@pytest.mark.parametrize("schema_name", REQUIRED_SCHEMAS)
def test_schema_file_exists(schema_name):
    path = ROOT / "docs" / "schemas" / schema_name
    assert path.exists(), f"Missing schema: docs/schemas/{schema_name}"

@pytest.mark.parametrize("schema_name", REQUIRED_SCHEMAS)
def test_schema_is_valid_json(schema_name):
    path = ROOT / "docs" / "schemas" / schema_name
    if not path.exists():
        pytest.skip(f"Schema not yet created: {schema_name}")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "$schema" in data
    assert "title" in data
    assert "type" in data or "properties" in data or "required" in data


# ---------------------------------------------------------------------------
# Interface presence tests
# ---------------------------------------------------------------------------

REQUIRED_INTERFACES = [
    "DocumentParser.md",
    "OCRProcessor.md",
    "StructureAnalyzer.md",
    "Chunker.md",
    "EmbeddingEngine.md",
    "LexicalIndexer.md",
    "VectorIndex.md",
    "HybridRetriever.md",
    "Ranker.md",
    "QuestionAnalyzer.md",
    "ConversationResolver.md",
    "QuestionRouter.md",
    "ExtractiveQA.md",
    "EvidenceValidator.md",
    "AnswerBuilder.md",
    "AnswerValidator.md",
    "ConfidenceEngine.md",
    "CitationEngine.md",
    "SafePresentationEngine.md",
]

@pytest.mark.parametrize("interface_name", REQUIRED_INTERFACES)
def test_interface_file_exists(interface_name):
    path = ROOT / "docs" / "interfaces" / interface_name
    assert path.exists(), f"Missing interface: docs/interfaces/{interface_name}"

@pytest.mark.parametrize("interface_name", REQUIRED_INTERFACES)
def test_interface_has_required_sections(interface_name):
    path = ROOT / "docs" / "interfaces" / interface_name
    if not path.exists():
        pytest.skip(f"Interface not yet created: {interface_name}")
    content = path.read_text(encoding="utf-8")
    assert "## Input" in content or "## Methods" in content, \
        f"{interface_name} missing Input or Methods section"
    assert "## Output" in content or "## Methods" in content, \
        f"{interface_name} missing Output section"
    assert "## Error States" in content, \
        f"{interface_name} missing Error States section"
    assert "## Performance Expectations" in content, \
        f"{interface_name} missing Performance Expectations section"


# ---------------------------------------------------------------------------
# Licensing document test
# ---------------------------------------------------------------------------

def test_licensing_doc_exists():
    path = ROOT / "docs" / "licensing.md"
    assert path.exists(), "docs/licensing.md is missing — required by Phase 0 licensing policy"

def test_licensing_doc_has_prohibited_section():
    path = ROOT / "docs" / "licensing.md"
    if not path.exists():
        pytest.skip("licensing.md not yet created")
    content = path.read_text(encoding="utf-8")
    assert "MuPDF" in content, "licensing.md must document MuPDF prohibition"
    assert "PyMuPDF" in content, "licensing.md must document PyMuPDF prohibition"
    assert "Camelot" in content, "licensing.md must document Camelot prohibition"

def test_licensing_doc_has_approved_section():
    path = ROOT / "docs" / "licensing.md"
    if not path.exists():
        pytest.skip("licensing.md not yet created")
    content = path.read_text(encoding="utf-8")
    assert "pdfplumber" in content, "licensing.md must document pdfplumber as approved"
    assert "onnxruntime" in content, "licensing.md must document onnxruntime as approved"


# ---------------------------------------------------------------------------
# Database schema test
# ---------------------------------------------------------------------------

def test_db_schema_exists():
    path = ROOT / "storage" / "schema.sql"
    assert path.exists(), "storage/schema.sql is missing"

def test_db_schema_has_required_tables():
    path = ROOT / "storage" / "schema.sql"
    if not path.exists():
        pytest.skip("schema.sql not yet created")
    content = path.read_text(encoding="utf-8")
    required_tables = [
        "Document", "Page", "Section", "Chunk",
        "IndexMetadata", "ModelRegistry",
        "ChatSession", "ConversationContext", "Message",
        "Citation", "Bookmark", "SchemaVersion"
    ]
    for table in required_tables:
        assert f"CREATE TABLE IF NOT EXISTS {table}" in content, \
            f"schema.sql missing table: {table}"

def test_db_schema_has_cascade_deletes():
    path = ROOT / "storage" / "schema.sql"
    if not path.exists():
        pytest.skip("schema.sql not yet created")
    content = path.read_text(encoding="utf-8")
    assert "ON DELETE CASCADE" in content, \
        "schema.sql must have CASCADE deletes (document deletion removes all related data)"

def test_db_schema_has_index_metadata_versioning():
    path = ROOT / "storage" / "schema.sql"
    if not path.exists():
        pytest.skip("schema.sql not yet created")
    content = path.read_text(encoding="utf-8")
    required_version_fields = [
        "embedding_model_id",
        "embedding_model_version",
        "embedding_dimension",
        "embedding_preprocessing_version",
        "chunking_strategy",
        "chunking_version",
        "distance_metric",
        "vector_index_type",
        "vector_index_version",
        "is_stale",
        "source_document_hash",
    ]
    for field in required_version_fields:
        assert field in content, \
            f"IndexMetadata in schema.sql missing versioning field: {field}"


# ---------------------------------------------------------------------------
# Model registry tests
# ---------------------------------------------------------------------------

def test_model_registry_exists():
    path = ROOT / "models" / "registry.json"
    assert path.exists(), "models/registry.json is missing"

def test_model_registry_has_required_models():
    path = ROOT / "models" / "registry.json"
    if not path.exists():
        pytest.skip("registry.json not yet created")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "models" in data
    model_ids = {m["model_id"] for m in data["models"]}
    required = {
        "bge-small-en-v1.5",
        "e5-small-v2",
        "all-MiniLM-L6-v2",
        "minilm-uncased-squad2",
        "ms-marco-MiniLM-L-6-v2",
    }
    missing = required - model_ids
    assert not missing, f"Model registry missing models: {missing}"

def test_model_registry_commercial_use():
    path = ROOT / "models" / "registry.json"
    if not path.exists():
        pytest.skip("registry.json not yet created")
    data = json.loads(path.read_text(encoding="utf-8"))
    for model in data["models"]:
        assert model.get("commercial_use", True) is True, \
            f"Model {model['model_id']} is not cleared for commercial use"

def test_model_registry_has_checksums():
    path = ROOT / "models" / "registry.json"
    if not path.exists():
        pytest.skip("registry.json not yet created")
    data = json.loads(path.read_text(encoding="utf-8"))
    for model in data["models"]:
        assert "checksum" in model and model["checksum"], \
            f"Model {model['model_id']} missing checksum"


# ---------------------------------------------------------------------------
# Evaluation harness metric tests
# ---------------------------------------------------------------------------

from benchmarks.harness import MetricComputer, BenchmarkDataset, QuestionType


class TestMetricComputer:
    def test_recall_at_1_hit(self):
        assert MetricComputer.recall_at_k([12, 15, 24], [12], 1) == 1.0

    def test_recall_at_1_miss(self):
        assert MetricComputer.recall_at_k([15, 24, 30], [12], 1) == 0.0

    def test_recall_at_5_partial(self):
        assert MetricComputer.recall_at_k([1, 2, 3, 12, 24], [12, 24, 99], 5) == pytest.approx(2/3, abs=0.01)

    def test_recall_empty_expected(self):
        assert MetricComputer.recall_at_k([1, 2, 3], [], 5) == 1.0

    def test_mrr_first(self):
        assert MetricComputer.mrr([12, 15, 24], [12]) == 1.0

    def test_mrr_second(self):
        assert MetricComputer.mrr([15, 12, 24], [12]) == pytest.approx(0.5, abs=0.01)

    def test_mrr_not_found(self):
        assert MetricComputer.mrr([1, 2, 3], [99]) == 0.0

    def test_ndcg_perfect(self):
        assert MetricComputer.ndcg([12, 24], [12, 24]) == 1.0

    def test_ndcg_none(self):
        assert MetricComputer.ndcg([1, 2, 3], [99, 100]) == 0.0

    def test_fragment_f1_full(self):
        f1 = MetricComputer.fragment_f1(
            "Hadoop provides scalability and fault tolerance.",
            ["scalability", "fault tolerance"]
        )
        assert f1 == 1.0

    def test_fragment_f1_partial(self):
        f1 = MetricComputer.fragment_f1(
            "Hadoop provides scalability.",
            ["scalability", "fault tolerance"]
        )
        assert f1 == pytest.approx(0.5, abs=0.01)

    def test_exact_match_pass(self):
        em = MetricComputer.exact_match("scalability and fault tolerance", ["scalability", "fault tolerance"])
        assert em == 1.0

    def test_exact_match_fail(self):
        em = MetricComputer.exact_match("only scalability here", ["scalability", "fault tolerance"])
        assert em == 0.0

    def test_exact_match_case_insensitive(self):
        em = MetricComputer.exact_match("SCALABILITY is great", ["scalability"])
        assert em == 1.0


class TestBenchmarkDataset:
    def test_sample_questions_created(self):
        samples = BenchmarkDataset.create_sample_questions()
        assert len(samples) >= 8

    def test_sample_questions_serializable(self):
        samples = BenchmarkDataset.create_sample_questions()
        for s in samples:
            d = s.to_dict()
            from benchmarks.harness import BenchmarkQuestion
            s2 = BenchmarkQuestion.from_dict(d)
            assert s2.question_id == s.question_id
            assert s2.question_type == s.question_type

    def test_sample_has_no_answer_question(self):
        samples = BenchmarkDataset.create_sample_questions()
        no_answer = [s for s in samples if not s.is_answerable]
        assert len(no_answer) >= 1, "Must have at least one no-answer question"

    def test_sample_has_follow_up(self):
        samples = BenchmarkDataset.create_sample_questions()
        follow_ups = [s for s in samples if s.follow_up_of is not None]
        assert len(follow_ups) >= 1, "Must have at least one follow-up question"

    def test_sample_covers_question_types(self):
        samples = BenchmarkDataset.create_sample_questions()
        types = {s.question_type for s in samples}
        required_types = {
            QuestionType.FACTUAL, QuestionType.DEFINITION,
            QuestionType.LIST, QuestionType.NO_ANSWER,
            QuestionType.FOLLOW_UP, QuestionType.AMBIGUOUS
        }
        missing = required_types - types
        assert not missing, f"Sample dataset missing question types: {missing}"

# Interface: ExtractiveQA

**Version**: 1.0.0
**Determinism**: APPROXIMATELY_DETERMINISTIC (ONNX inference is deterministic given same model+input)
**Implemented in Phase**: 10

---

## Purpose

Given a question and a set of retrieved passages, locate the exact answer span
within the passage text. The answer must originate character-for-character from
the document. No rewriting, no synthesis.

---

## Input

```
extract(
  question: string,               -- resolved query text
  passages: RankedChunk[],        -- top-k retrieved chunks
  options: QAOptions
) → AnswerSpan[]

QAOptions:
  max_answer_length_tokens: int   -- default: 50
  min_confidence: float           -- discard spans below this. Default: 0.3
  max_passages: int               -- max passages to run model on. Default: 5
  return_null_if_unanswerable: boolean -- squad2 null-answer detection. Default: true
```

---

## Output — AnswerSpan

```
AnswerSpan:
  text: string                    -- exact substring of passage.text
  passage_chunk_id: UUID          -- which chunk this span came from
  document_id: UUID
  page_number: int
  start_char: int                 -- character offset within chunk.text
  end_char: int
  confidence: float               -- QA model confidence (logit-derived)
  is_impossible: boolean          -- true if model says no answer exists
  bounding_boxes: BoundingBox[]   -- derived from chunk bbox + char offsets
```

---

## Model

```
Primary: deepset/minilm-uncased-squad2
  Format: ONNX INT8
  Size: ~45 MB (quantized)
  Architecture: MiniLM fine-tuned on SQuAD v2
  SQuAD v2 training: supports null-answer detection
  License: Apache 2.0

Inputs to model:
  [CLS] question [SEP] passage [SEP]

Outputs:
  start_logits: float[] (per-token)
  end_logits: float[] (per-token)
  has_answer_score: float (SQuAD2 null classifier)
```

---

## Impossible Answer Handling (SQuAD2)

```
if has_answer_score < null_threshold:
    is_impossible = true
    text = ""
    confidence = 0.0
else:
    extract best (start, end) span
    text = passage[start:end]
```

---

## Error States

```
MODEL_NOT_LOADED          -- ONNX model not loaded; caller must load first
NO_PASSAGES               -- passages list is empty
SEQUENCE_TOO_LONG         -- question+passage exceeds model max_length (512 tokens)
                           -- truncate passage, not question
INFERENCE_FAILED          -- ONNX runtime error
LOW_CONFIDENCE            -- all spans below min_confidence; return empty list
```

---

## Performance Expectations

| Device Class | Target (per passage) |
|---|---|
| Desktop | < 30 ms |
| High-end Android (INT8) | < 80 ms |
| Mid-range Android (INT8) | < 150 ms |
| Low-end Android (INT8) | < 300 ms |

---

## Notes

- The answer text MUST be an exact substring of the passage. Never modify the extracted span.
- If sequence exceeds 512 tokens, truncate the passage (not the question) from the end.
- Run on at most max_passages passages. Rank by fusion_score first; take top ones.
- This interface is ONLY invoked for FACT_QA routes. LIST, SUMMARY, TABLE use different extractors.
- The model is loaded lazily. Do not keep it in memory during indexing.

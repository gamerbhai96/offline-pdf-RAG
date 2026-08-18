"""
Citation Engine — Phase 13

Implements CitationEngine interface (/docs/interfaces/CitationEngine.md).

Produces human-readable citations from ValidatedEvidence, linking each
answer point back to its source page and bounding box.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Citation:
    citation_id: str
    chunk_id: str
    document_id: str
    document_title: Optional[str]
    page_number: int
    short_quote: str              # ≤ 100 chars, exact source text excerpt
    bounding_boxes: list[dict] = field(default_factory=list)  # [{x0,y0,x1,y1,page}]

    def to_inline_ref(self) -> str:
        """Format as inline citation: [p. 5]"""
        return f"[p. {self.page_number}]"

    def to_full_ref(self) -> str:
        """Format as full citation line: [Source: filename | Page X]"""
        title = self.document_title or "Document"
        return f"[Source: {title} | Page {self.page_number}]"

    def to_dict(self) -> dict:
        return {
            "citation_id": self.citation_id,
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "document_title": self.document_title,
            "page_number": self.page_number,
            "short_quote": self.short_quote,
            "bounding_boxes": self.bounding_boxes,
        }


class CitationEngine:
    """
    Produces one Citation per unique (chunk_id, page_number) combination.
    Deduplicates: if two answer points reference the same chunk, one citation.
    """

    def generate(
        self,
        evidence,
        document_titles: Optional[dict[str, str]] = None,
    ) -> dict[str, Citation]:
        """
        Generate citations for all evidence chunks.

        Args:
            evidence: List of ValidatedEvidence.
            document_titles: {document_id: title} lookup.

        Returns:
            Dict mapping chunk_id → Citation.
        """
        citations: dict[str, Citation] = {}
        for ev in evidence:
            if ev.chunk_id in citations:
                continue
            title = (document_titles or {}).get(ev.document_id)
            try:
                page_num = int(ev.page_id)
            except (ValueError, TypeError):
                page_num = 0

            short_quote = self._extract_quote(ev.text)
            citations[ev.chunk_id] = Citation(
                citation_id=f"cite_{ev.chunk_id[:8]}",
                chunk_id=ev.chunk_id,
                document_id=ev.document_id,
                document_title=title,
                page_number=page_num,
                short_quote=short_quote,
                bounding_boxes=ev.bounding_boxes[:3],  # cap to 3 boxes
            )
        return citations

    def _extract_quote(self, text: str, max_len: int = 100) -> str:
        """Extract a clean short quote from chunk text."""
        # Take first sentence
        sentences = re.split(r"(?<=[.!?])\s+", text.strip())
        if sentences:
            quote = sentences[0][:max_len].strip()
            if len(sentences[0]) > max_len:
                quote = quote.rstrip() + "…"
            return quote
        return text[:max_len].strip()

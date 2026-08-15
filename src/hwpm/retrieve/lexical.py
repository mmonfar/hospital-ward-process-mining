"""Lexical (BM25) retrieval — pure Python, no optional dependency.

This is what `--lexical-only` runs, and what the G2 measurement in ADR-0007
is scored against. Kept dependency-free deliberately: `hwpm govern` and
`hwpm context search --lexical-only` must both work with neither `fastembed`
nor `sqlite-vec` installed (SPEC-007 criteria 8-9).

Indexes `heading_path + text` (not `text` alone) so that a query naming a
document ("SPEC-004", "ADR-0005") can match via the heading even when the
identifier itself does not appear in the passage body. The verbatim
guarantee is unaffected: `Chunk.text` is untouched by this, it is only used
as additional retrieval signal.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from hwpm.retrieve.chunk import Chunk

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Standard Okapi BM25 constants (Robertson/Sparck Jones); not tuned on the
# labelled query set, per SPEC-007 modelling assumption 3's spirit — this
# retriever exists to *measure* the gate, not to be optimised toward a
# predetermined answer.
_K1 = 1.5
_B = 0.75


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


@dataclass
class LexicalIndex:
    chunks: list[Chunk] = field(default_factory=list)
    _doc_tokens: list[list[str]] = field(default_factory=list, repr=False)
    _doc_len: list[int] = field(default_factory=list, repr=False)
    _df: dict[str, int] = field(default_factory=dict, repr=False)
    _postings: dict[str, dict[int, int]] = field(default_factory=dict, repr=False)
    _avgdl: float = 0.0

    @classmethod
    def build(cls, chunks: list[Chunk]) -> LexicalIndex:
        idx = cls(chunks=list(chunks))
        df: dict[str, int] = {}
        postings: dict[str, dict[int, int]] = {}
        doc_tokens: list[list[str]] = []
        doc_len: list[int] = []
        for doc_id, chunk in enumerate(idx.chunks):
            toks = tokenize(f"{chunk.heading_path}\n{chunk.text}")
            doc_tokens.append(toks)
            doc_len.append(len(toks))
            seen: set[str] = set()
            counts: dict[str, int] = {}
            for t in toks:
                counts[t] = counts.get(t, 0) + 1
            for t, c in counts.items():
                postings.setdefault(t, {})[doc_id] = c
                if t not in seen:
                    df[t] = df.get(t, 0) + 1
                    seen.add(t)
        idx._doc_tokens = doc_tokens
        idx._doc_len = doc_len
        idx._df = df
        idx._postings = postings
        idx._avgdl = (sum(doc_len) / len(doc_len)) if doc_len else 0.0
        return idx

    def _idf(self, term: str) -> float:
        n = len(self.chunks)
        df = self._df.get(term, 0)
        # BM25 idf with the +1 floor so unseen query terms score 0, not
        # negative — a query term absent from the corpus should not
        # *penalise* a document relative to one that also lacks it.
        return max(0.0, math.log((n - df + 0.5) / (df + 0.5) + 1.0))

    def search(self, query: str, k: int = 8) -> list[tuple[Chunk, float]]:
        q_terms = tokenize(query)
        if not q_terms or not self.chunks:
            return []
        scores: dict[int, float] = {}
        for term in q_terms:
            postings = self._postings.get(term)
            if not postings:
                continue
            idf = self._idf(term)
            if idf <= 0:
                continue
            for doc_id, tf in postings.items():
                dl = self._doc_len[doc_id]
                denom = tf + _K1 * (1 - _B + _B * dl / (self._avgdl or 1.0))
                score = idf * (tf * (_K1 + 1)) / (denom or 1.0)
                scores[doc_id] = scores.get(doc_id, 0.0) + score
        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:k]
        return [(self.chunks[doc_id], score) for doc_id, score in ranked]

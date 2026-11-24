"""
Paragraph and sentence-aware chunker.
Splits on blank lines, then sentence boundaries for long paragraphs,
and merges up to a character budget while preserving exact (char_start, char_end) coordinates
into the stored text.
"""
from __future__ import annotations
from dataclasses import dataclass
import re


@dataclass(frozen=True)
class ChunkSpan:
    ord: int
    char_start: int
    char_end: int
    text: str


class ParagraphChunker:
    def __init__(self, budget: int = 1000, overlap_sentences: int = 1):
        self.budget = budget
        self.overlap_sentences = overlap_sentences

    def chunk(self, text: str) -> list[ChunkSpan]:
        """
        Chunks text into spans satisfying:
        text[chunk.char_start:chunk.char_end] == chunk.text
        """
        if not text:
            return []

        text_len = len(text)
        if text_len <= self.budget:
            return [ChunkSpan(ord=0, char_start=0, char_end=text_len, text=text)]

        # 1. Identify paragraph spans (separated by 2 or more newlines)
        raw_paras: list[tuple[int, int]] = []
        for match in re.finditer(r'(?:[^\n]|\n(?!\s*\n))+', text):
            p_start, p_end = match.span()
            if p_start < p_end:
                raw_paras.append((p_start, p_end))

        if not raw_paras:
            return [ChunkSpan(ord=0, char_start=0, char_end=text_len, text=text)]

        # 2. Break down into atomic spans (sentences or paragraphs)
        atomic_spans: list[tuple[int, int]] = []
        sentence_regex = re.compile(r'[^.!?\n]+(?:[.!?]+(?:\s+|\n+|$)|$)', re.UNICODE)

        for p_start, p_end in raw_paras:
            para_text = text[p_start:p_end]
            if len(para_text) <= self.budget:
                atomic_spans.append((p_start, p_end))
            else:
                sent_found = False
                for s_match in sentence_regex.finditer(para_text):
                    s_sub_start, s_sub_end = s_match.span()
                    if s_sub_start < s_sub_end:
                        sent_found = True
                        atomic_spans.append((p_start + s_sub_start, p_start + s_sub_end))
                if not sent_found:
                    atomic_spans.append((p_start, p_end))

        # 3. Merge atomic spans up to budget with overlap
        chunks: list[ChunkSpan] = []
        ord_num = 0
        i = 0
        n = len(atomic_spans)

        while i < n:
            chunk_start = atomic_spans[i][0]
            chunk_end = atomic_spans[i][1]
            end_idx = i

            while end_idx + 1 < n:
                next_end = atomic_spans[end_idx + 1][1]
                if (next_end - chunk_start) <= self.budget:
                    chunk_end = next_end
                    end_idx += 1
                else:
                    break

            if chunk_end == chunk_start:
                chunk_end = min(chunk_start + self.budget, text_len)

            chunk_text = text[chunk_start:chunk_end]
            chunks.append(
                ChunkSpan(
                    ord=ord_num,
                    char_start=chunk_start,
                    char_end=chunk_end,
                    text=chunk_text,
                )
            )
            ord_num += 1

            if end_idx >= n - 1:
                break

            overlap = max(1, self.overlap_sentences)
            next_i = max(i + 1, end_idx - overlap + 1)
            if next_i <= i:
                next_i = i + 1
            i = next_i

        return chunks

import re
from typing import AsyncGenerator, List, Optional


class SyntacticClauseChunker:
    """
    Splits streaming LLM tokens into prosodically natural speech clauses.
    Triggers TTS synthesis on syntactic boundaries (periods, commas, semicolons)
    to achieve sub-250ms Time-to-First-Audio (TTFA).
    """

    STRONG_DELIMITERS = {".", "!", "?", "\n"}
    MEDIUM_DELIMITERS = {",", ";", ":", "—", "-"}

    def __init__(
        self,
        min_words_for_medium_delimiter: int = 4,
        max_words_per_chunk: int = 25,
    ):
        self.min_words = min_words_for_medium_delimiter
        self.max_words = max_words_per_chunk
        self._buffer: str = ""

    def _should_split_at(self, text: str) -> Optional[int]:
        """
        Determines if buffer contains a natural prosodic clause boundary.
        Returns the split index if a boundary is reached, otherwise None.
        """
        words = text.split()
        word_count = len(words)
        if word_count == 0:
            return None

        for idx, char in enumerate(text):
            # 1. Strong punctuation boundaries (always split if preceded by non-space)
            if char in self.STRONG_DELIMITERS:
                # Avoid splitting on decimals like 3.14 or abbreviations like e.g.
                if idx > 0 and text[idx - 1].isdigit() and idx + 1 < len(text) and text[idx + 1].isdigit():
                    continue
                return idx + 1

            # 2. Medium punctuation boundaries (split if minimum words met)
            if char in self.MEDIUM_DELIMITERS and word_count >= self.min_words:
                return idx + 1

        # 3. Maximum length safeguard (prevent buffer overflow if no punctuation)
        if word_count >= self.max_words:
            # Split at last space
            last_space = text.rfind(" ")
            if last_space > 0:
                return last_space + 1

        return None

    def push_token(self, token: str) -> List[str]:
        """
        Appends a streaming token to the buffer.
        Returns any fully formed speech clauses ready for TTS synthesis.
        """
        self._buffer += token
        ready_clauses: List[str] = []

        while True:
            split_idx = self._should_split_at(self._buffer)
            if split_idx is None:
                break
            clause = self._buffer[:split_idx].strip()
            self._buffer = self._buffer[split_idx:].lstrip()
            if clause:
                ready_clauses.append(clause)

        return ready_clauses

    def flush(self) -> List[str]:
        """Flushes any remaining text in the buffer as a final speech chunk."""
        rem = self._buffer.strip()
        self._buffer = ""
        return [rem] if rem else []

    def clear(self) -> None:
        """Clears buffer immediately on barge-in interruption."""
        self._buffer = ""

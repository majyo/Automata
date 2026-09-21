"""Bounded head/tail text accumulation shared by tool and process output."""


class HeadTailTextBuffer:
    """Keeps the head and tail of a stream, marking the elision.

    Process output is most useful at both ends (a banner and the final
    error), so truncation keeps half the budget for each rather than
    dropping the tail.
    """

    _MARKER = "\n... output truncated ...\n"

    def __init__(self, max_chars: int) -> None:
        self.max_chars = max(0, max_chars)
        self._value = ""
        self._head = ""
        self._tail = ""
        self.truncated = False

    def append(self, text: str) -> None:
        if not text:
            return
        if self.max_chars <= 0:
            self.truncated = True
            return
        if not self.truncated:
            combined = self._value + text
            if len(combined) <= self.max_chars:
                self._value = combined
                return
            self.truncated = True
            marker = self._marker()
            available = max(0, self.max_chars - len(marker))
            head_chars = available // 2
            tail_chars = available - head_chars
            self._head = combined[:head_chars]
            self._tail = combined[-tail_chars:] if tail_chars else ""
            self._value = ""
            return
        tail_chars = self._tail_limit()
        if tail_chars:
            self._tail = (self._tail + text)[-tail_chars:]

    @property
    def text(self) -> str:
        if not self.truncated:
            return self._value
        return f"{self._head}{self._marker()}{self._tail}"

    def _marker(self) -> str:
        if self.max_chars < len(self._MARKER) + 2:
            return ""
        return self._MARKER

    def _tail_limit(self) -> int:
        available = max(0, self.max_chars - len(self._marker()))
        return available - available // 2

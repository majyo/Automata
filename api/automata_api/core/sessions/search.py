"""Context-search limits and storage-neutral failure."""

DEFAULT_CONTEXT_SEARCH_LIMIT = 5


MAX_CONTEXT_SEARCH_LIMIT = 8


MAX_CONTEXT_SEARCH_QUERY_CHARS = 512


class ContextSearchError(RuntimeError):
    pass

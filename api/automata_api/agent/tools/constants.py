"""Shared numeric bounds for tool execution.

These are part of the tool contract (they decide how much output a model
sees), so they live in one place instead of being redefined per tool.
"""

OUTPUT_LIMIT = 20_000
DEFAULT_EXEC_OUTPUT_CHARS = OUTPUT_LIMIT
MAX_EXEC_OUTPUT_CHARS = 60_000
SEARCH_TIMEOUT_SECONDS = 30.0
FILE_READ_LIMIT = 120_000
PROCESS_OUTPUT_CHUNK_BYTES = 8192
SUPPORTED_EXEC_SHELLS = ("bash", "powershell")

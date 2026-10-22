"""Phase 1 — Document parsing: OCR text → Document/Page/Block(text|table), giữ provenance."""

from steel_xai.parsing.document import (
    Block,
    Document,
    Page,
    ParseError,
    TableCell,
    parse_file,
    parse_text,
)

__all__ = ["Block", "Document", "Page", "ParseError", "TableCell", "parse_file", "parse_text"]

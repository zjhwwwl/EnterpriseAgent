"""Character-bounded Chinese-friendly chunks that never mix citation sections."""

from dataclasses import dataclass

from enterprise_agent.domain_errors import BusinessError
from enterprise_agent.rag.parsing import ParsedSection, SourceLocation

MAX_CHUNKS = 20_000
_SENTENCE_ENDS = frozenset("。！？!?；;\n")


@dataclass(frozen=True, slots=True)
class ParsedChunk:
    text: str
    source_location: SourceLocation


def _choose_end(text: str, start: int, hard_end: int, overlap: int, size: int) -> int:
    if hard_end == len(text):
        return hard_end
    # Keep useful chunk lengths, and always advance even with large overlap.
    minimum_end = max(start + size // 2, start + overlap + 1)
    for index in range(hard_end - 1, minimum_end - 2, -1):
        char = text[index]
        if char in _SENTENCE_ENDS or (
            char == "." and index + 1 < len(text) and text[index + 1].isspace()
        ):
            return index + 1
    return hard_end


def _location(section: ParsedSection, text: str, start: int, end: int) -> SourceLocation:
    location = dict(section.source_location)
    location.update({"char_start": start, "char_end": end})
    line_start = section.source_location.get("line_start")
    if type(line_start) is int:
        location["line_start"] = line_start + text.count("\n", 0, start)
        location["line_end"] = line_start + text.count("\n", 0, max(start, end - 1))
    return location


def chunk_sections(
    sections: list[ParsedSection], *, chunk_size: int = 500, chunk_overlap: int = 80
) -> list[ParsedChunk]:
    """Chunk by characters, prefer late sentence boundaries, preserve exact slices.

    char_start is zero-based and char_end is exclusive within the parsed section.
    A character budget is deliberate: Chinese is not split into presumed words,
    and the embedding layer separately enforces the actual model token budget.
    """
    if type(chunk_size) is not int or chunk_size < 1:
        raise ValueError("chunk_size must be a positive integer")
    if type(chunk_overlap) is not int or not 0 <= chunk_overlap < chunk_size:
        raise ValueError("chunk_overlap must be a nonnegative integer smaller than chunk_size")
    chunks = []
    for section in sections:
        text = section.text
        if not text.strip():
            continue
        start = 0
        while start < len(text):
            end = _choose_end(
                text, start, min(start + chunk_size, len(text)), chunk_overlap, chunk_size
            )
            fragment = text[start:end]
            if fragment.strip():
                chunks.append(ParsedChunk(fragment, _location(section, text, start, end)))
                if len(chunks) > MAX_CHUNKS:
                    raise BusinessError(
                        "document_too_large",
                        "文档分块数量超过上限，请减小文档或调整分块设置。",
                        422,
                    )
            if end == len(text):
                break
            start = end - chunk_overlap
    return chunks

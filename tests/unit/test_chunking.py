"""Chunk coverage, size limits and citations are checked without an embedding model."""

import pytest

from enterprise_agent.domain_errors import BusinessError
from enterprise_agent.rag import chunking
from enterprise_agent.rag.chunking import chunk_sections
from enterprise_agent.rag.parsing import ParsedSection


def test_short_section_is_kept_with_exact_text_and_copied_source_metadata():
    original = ParsedSection("退款申请需要客户确认。", {"page": 2})
    chunks = chunk_sections([original])
    assert len(chunks) == 1
    assert chunks[0].text == original.text
    assert chunks[0].source_location == {"page": 2, "char_start": 0, "char_end": len(original.text)}
    chunks[0].source_location["page"] = 9
    assert original.source_location == {"page": 2}


def test_no_overlap_chunks_cover_every_character_without_exceeding_size():
    text = "七天内可申请退款。用户必须确认申请金额。客服审核后模拟完成退款。" * 30
    chunks = chunk_sections([ParsedSection(text, {"paragraph": 1})], chunk_size=45, chunk_overlap=0)
    assert "".join(chunk.text for chunk in chunks) == text
    assert all(0 < len(chunk.text) <= 45 for chunk in chunks)
    assert len(chunks) > 1
    assert all(chunk.source_location["paragraph"] == 1 for chunk in chunks)


def test_overlap_preserves_full_coverage_and_matches_the_recorded_offsets():
    text = "商品售后政策需要结合数据库订单状态和物流事实判断。退款期限从签收时间开始计算。" * 20
    chunks = chunk_sections([ParsedSection(text, {"page": 1})], chunk_size=50, chunk_overlap=8)
    assert chunks[0].source_location["char_start"] == 0
    assert chunks[-1].source_location["char_end"] == len(text)
    reconstructed = chunks[0].text
    for previous, current in zip(chunks, chunks[1:], strict=False):
        previous_end = previous.source_location["char_end"]
        current_start = current.source_location["char_start"]
        assert previous_end - current_start == 8
        assert current.source_location["char_start"] > previous.source_location["char_start"]
        assert previous.text[-8:] == current.text[:8]
        reconstructed += current.text[8:]
    assert reconstructed == text
    for chunk in chunks:
        assert len(chunk.text) <= 50
        start = chunk.source_location["char_start"]
        end = chunk.source_location["char_end"]
        assert chunk.text == text[start:end]


def test_late_chinese_sentence_boundary_is_preferred_over_cutting_mid_sentence():
    text = "用户确认退款申请。" + "客服随后审核申请。"
    chunks = chunk_sections([ParsedSection(text, {"page": 1})], chunk_size=12, chunk_overlap=0)
    assert chunks[0].text == "用户确认退款申请。"
    assert "".join(chunk.text for chunk in chunks) == text


def test_long_sentence_without_punctuation_uses_a_hard_character_bound():
    text = "政策" * 101
    chunks = chunk_sections([ParsedSection(text, {"page": 1})], chunk_size=20, chunk_overlap=3)
    assert len(chunks) > 1
    assert all(len(chunk.text) <= 20 for chunk in chunks)
    assert chunks[-1].source_location["char_end"] == len(text)


def test_pages_and_docx_cells_are_never_combined_into_one_chunk():
    sections = [
        ParsedSection("第一页政策", {"page": 1}),
        ParsedSection("第二页政策", {"page": 2}),
        ParsedSection("食品例外", {"table": 1, "row": 2, "cell": 1, "paragraph": 1}),
    ]
    chunks = chunk_sections(sections, chunk_size=100, chunk_overlap=10)
    assert [chunk.text for chunk in chunks] == [section.text for section in sections]
    assert [chunk.source_location.get("page") for chunk in chunks] == [1, 2, None]
    assert chunks[2].source_location["row"] == 2
    assert chunks[2].source_location["cell"] == 1


def test_text_line_ranges_are_refined_for_each_chunk():
    text = "第一行。\n第二行。\n第三行。"
    sections = [ParsedSection(text, {"line_start": 10, "line_end": 12})]
    chunks = chunk_sections(sections, chunk_size=6, chunk_overlap=0)
    assert [chunk.text for chunk in chunks] == ["第一行。\n", "第二行。\n", "第三行。"]
    assert [
        (chunk.source_location["line_start"], chunk.source_location["line_end"]) for chunk in chunks
    ] == [(10, 10), (11, 11), (12, 12)]


def test_blank_sections_produce_no_chunks_and_do_not_create_empty_metadata():
    assert chunk_sections([]) == []
    assert chunk_sections([ParsedSection(" \t\n ", {"page": 1})]) == []


@pytest.mark.parametrize("size", [1, 2, 3, 7])
def test_high_overlap_still_advances_and_terminates(size):
    text = "一句。另句。连续文本。"
    chunks = chunk_sections(
        [ParsedSection(text, {"paragraph": 1})], chunk_size=size, chunk_overlap=size - 1
    )
    assert chunks[-1].source_location["char_end"] == len(text)
    assert len(chunks) <= len(text)
    starts = [chunk.source_location["char_start"] for chunk in chunks]
    assert starts == sorted(set(starts))
    assert all(0 < len(chunk.text) <= size for chunk in chunks)


@pytest.mark.parametrize(
    ("size", "overlap"),
    [(0, 0), (-1, 0), (True, 0), (5.0, 0), (5, -1), (5, 5), (5, 6), (5, True), (5, 0.5)],
)
def test_invalid_chunk_configuration_is_rejected(size, overlap):
    with pytest.raises(ValueError):
        chunk_sections([], chunk_size=size, chunk_overlap=overlap)


def test_excessive_chunk_counts_fail_instead_of_exhausting_memory(monkeypatch):
    monkeypatch.setattr(chunking, "MAX_CHUNKS", 2)
    with pytest.raises(BusinessError) as caught:
        chunk_sections(
            [ParsedSection("连续无标点文本", {"page": 1})], chunk_size=2, chunk_overlap=0
        )
    assert caught.value.status_code == 422
    assert caught.value.code == "document_too_large"

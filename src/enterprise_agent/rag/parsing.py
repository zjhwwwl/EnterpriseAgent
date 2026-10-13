"""Extract bounded text from uploaded documents without OCR or filesystem extraction."""

from collections.abc import Iterator
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath
from unicodedata import category
from xml.etree import ElementTree
from xml.parsers import expat
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from pypdf import PdfReader

from enterprise_agent.domain_errors import BusinessError

type SourceLocation = dict[str, int | str]

MAX_INPUT_BYTES = 16 * 1024 * 1024
MAX_EXTRACTED_CHARS = 2_000_000
MAX_PDF_PAGES = 500
MAX_PDF_PAGE_STREAM_BYTES = 8 * 1024 * 1024
MAX_DOCX_MEMBERS = 2000
MAX_DOCX_EXPANDED_BYTES = 32 * 1024 * 1024
MAX_DOCX_XML_BYTES = 8 * 1024 * 1024
MAX_TABLE_DEPTH = 10
_WORD_MAIN_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"


@dataclass(frozen=True, slots=True)
class ParsedSection:
    text: str
    source_location: SourceLocation


def _failure(message: str, *, code: str = "document_parse_failed") -> BusinessError:
    return BusinessError(code, message, 422)


def _validate_text(text: str) -> None:
    if "\ufffd" in text or any(category(char) == "Cc" and char not in "\n\r\t\f" for char in text):
        raise _failure("文档含替换字符或二进制控制字符，无法可靠解析。")


def _normalize_text(text: str) -> str:
    _validate_text(text)
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\f", "\n").strip()


def _parse_pdf(content: bytes) -> Iterator[ParsedSection]:
    if not content.startswith(b"%PDF-"):
        raise _failure("文件内容与 PDF 格式不匹配。")
    reader = PdfReader(BytesIO(content), strict=True)
    if reader.is_encrypted:
        raise _failure("不支持加密 PDF，请先解密后上传。")
    if len(reader.pages) > MAX_PDF_PAGES:
        raise _failure("PDF 页数超过解析上限。", code="document_too_large")
    for page_number, page in enumerate(reader.pages, start=1):
        # pypdf documents a large memory cost for unusually large page streams.
        # This check bounds extraction work after stream decoding; it is not an
        # operating-system memory sandbox for hostile PDF decompression.
        stream = page.get_contents()
        if stream is not None and len(stream.get_data()) > MAX_PDF_PAGE_STREAM_BYTES:
            raise _failure("PDF 单页文本流超过解析上限。", code="document_too_large")
        yield ParsedSection(page.extract_text() or "", {"page": page_number})


def _reject_xml_declaration(*_args):
    raise _failure("DOCX 不允许包含 DTD 或实体声明。")


def _check_xml(content: bytes) -> None:
    parser = expat.ParserCreate()
    parser.StartDoctypeDeclHandler = _reject_xml_declaration
    parser.EntityDeclHandler = _reject_xml_declaration
    parser.ExternalEntityRefHandler = _reject_xml_declaration
    parser.Parse(content, True)


def _validate_docx(content: bytes) -> None:
    if not content.startswith(b"PK\x03\x04"):
        raise _failure("文件内容与 DOCX 格式不匹配。")
    with ZipFile(BytesIO(content)) as archive:
        members = archive.infolist()
        if len(members) > MAX_DOCX_MEMBERS:
            raise _failure("DOCX 压缩包成员数超过上限。", code="document_too_large")
        if sum(member.file_size for member in members) > MAX_DOCX_EXPANDED_BYTES:
            raise _failure("DOCX 展开后大小超过上限。", code="document_too_large")
        names = [member.filename for member in members]
        if len(set(names)) != len(names):
            raise _failure("DOCX 含重复压缩包路径。")
        required = {"[Content_Types].xml", "_rels/.rels", "word/document.xml"}
        if not required.issubset(names):
            raise _failure("DOCX 缺少必要的文档组件。")
        for member in members:
            path = PurePosixPath(member.filename)
            if (
                path.is_absolute()
                or ".." in path.parts
                or "\\" in member.filename
                or ":" in member.filename
                or member.flag_bits & 1
                or member.compress_type not in (ZIP_STORED, ZIP_DEFLATED)
            ):
                raise _failure("DOCX 含不安全或不支持的压缩包成员。")
            if member.filename.lower().endswith("vbaproject.bin"):
                raise _failure("不支持包含宏的 Office 文档。")
            if member.filename.endswith((".xml", ".rels")):
                if member.file_size > MAX_DOCX_XML_BYTES:
                    raise _failure("DOCX XML 组件超过上限。", code="document_too_large")
                with archive.open(member) as xml_file:
                    xml_content = xml_file.read(MAX_DOCX_XML_BYTES + 1)
                if len(xml_content) > MAX_DOCX_XML_BYTES:
                    raise _failure("DOCX XML 组件超过上限。", code="document_too_large")
                _check_xml(xml_content)
        if archive.testzip() is not None:
            raise _failure("DOCX 压缩包校验失败。")
        content_types = ElementTree.fromstring(archive.read("[Content_Types].xml"))
        if not any(
            child.get("PartName") == "/word/document.xml"
            and child.get("ContentType") == _WORD_MAIN_TYPE
            for child in content_types
        ):
            raise _failure("压缩包不是支持的 Word DOCX 文档。")


def _table_sections(
    table: Table, table_number: int, *, path: str, depth: int = 0
) -> Iterator[ParsedSection]:
    if depth > MAX_TABLE_DEPTH:
        raise _failure("DOCX 表格嵌套超过上限。", code="document_too_large")
    seen_cells = set()
    for row_number, row in enumerate(table.rows, start=1):
        for cell_number, cell in enumerate(row.cells, start=1):
            # A merged cell can be exposed at multiple grid positions.
            if cell._tc in seen_cells:
                continue
            seen_cells.add(cell._tc)
            paragraph_number = 0
            nested_number = 0
            for block in cell.iter_inner_content():
                if isinstance(block, Paragraph):
                    paragraph_number += 1
                    location: SourceLocation = {
                        "table": table_number,
                        "row": row_number,
                        "cell": cell_number,
                        "paragraph": paragraph_number,
                    }
                    if depth:
                        location["table_path"] = path
                    yield ParsedSection(block.text, location)
                elif isinstance(block, Table):
                    nested_number += 1
                    nested_path = f"{path}/r{row_number}c{cell_number}/t{nested_number}"
                    yield from _table_sections(
                        block, table_number, path=nested_path, depth=depth + 1
                    )


def _parse_docx(content: bytes) -> Iterator[ParsedSection]:
    _validate_docx(content)
    document = Document(BytesIO(content))
    paragraph_number = 0
    table_number = 0
    for block in document.iter_inner_content():
        if isinstance(block, Paragraph):
            paragraph_number += 1
            yield ParsedSection(block.text, {"paragraph": paragraph_number})
        elif isinstance(block, Table):
            table_number += 1
            yield from _table_sections(block, table_number, path=str(table_number))


def _parse_text(content: bytes) -> Iterator[ParsedSection]:
    if content.startswith((b"%PDF-", b"PK\x03\x04", b"\xd0\xcf\x11\xe0")):
        raise _failure("二进制文档不能作为 TXT 或 Markdown 上传。")
    # No encoding guesses: failed decoding must not silently replace Chinese text.
    decoded = content.decode("utf-8-sig", errors="strict")
    _validate_text(decoded)
    block: list[str] = []
    line_start = 1
    last_line = 0
    for line_number, line in enumerate(decoded.splitlines(), start=1):
        if line.strip():
            if not block:
                line_start = line_number
            block.append(line)
            last_line = line_number
        elif block:
            yield ParsedSection("\n".join(block), {"line_start": line_start, "line_end": last_line})
            block = []
    if block:
        yield ParsedSection("\n".join(block), {"line_start": line_start, "line_end": last_line})


def parse_document(content: bytes, file_type: str) -> list[ParsedSection]:
    """Parse supported bytes and retain one-based page, paragraph or line locations.

    DOCX covers main-body paragraphs and tables, including nested tables; headers,
    footers, tracked revisions and text boxes are outside this first parser.
    PDF extraction reads its existing text layer and does not perform OCR.
    """
    if not isinstance(content, bytes) or not content:
        raise _failure("文档内容不能为空。")
    if len(content) > MAX_INPUT_BYTES:
        raise _failure("文档大小超过解析上限。", code="document_too_large")
    normalized_type = file_type.lower().strip() if isinstance(file_type, str) else ""
    if normalized_type == "md":
        normalized_type = "markdown"
    if normalized_type not in {"pdf", "docx", "txt", "markdown"}:
        raise _failure("仅支持 PDF、DOCX、TXT 和 Markdown。", code="unsupported_document_type")
    try:
        if normalized_type == "pdf":
            source = _parse_pdf(content)
        elif normalized_type == "docx":
            source = _parse_docx(content)
        else:
            source = _parse_text(content)
        sections = []
        total_chars = 0
        for section in source:
            text = _normalize_text(section.text)
            if not text:
                continue
            total_chars += len(text)
            if total_chars > MAX_EXTRACTED_CHARS:
                raise _failure("文档提取文本超过上限。", code="document_too_large")
            sections.append(ParsedSection(text, dict(section.source_location)))
        if not sections:
            if normalized_type == "pdf":
                raise _failure("PDF 未提取到文本；扫描件需要 OCR，当前版本不支持 OCR。")
            raise _failure("文档没有可索引的文本。")
        return sections
    except BusinessError:
        raise
    except Exception:
        # Library failures often contain document fragments. Expose a stable,
        # safe boundary error instead of leaking uploaded content or tracebacks.
        raise _failure(
            "文档解析失败，请检查文件是否损坏、格式是否正确及文本编码是否为 UTF-8。"
        ) from None

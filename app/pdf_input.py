"""Normalize PDFs whose object indexes cannot be read reliably by MuPDF."""

import functools
import io
from asyncio import CancelledError
from pathlib import Path
import tempfile

import pikepdf
import pymupdf


def normalize_pdf_bytes(data):
    with pymupdf.open(stream=data, filetype="pdf") as document:
        requires_rewrite = document.is_repaired
    if not requires_rewrite:
        return data
    # QPDF rebuilds the index without changing the page content streams.
    # MuPDF's garbage collection alone leaves a page-tree cycle in the reported file.
    with pikepdf.open(io.BytesIO(data)) as document:
        output = io.BytesIO()
        document.save(output)
    return output.getvalue()


def prepare_pdf_file(path):
    if path is None:
        return None
    source = Path(path)
    data = source.read_bytes()
    normalized = normalize_pdf_bytes(data)
    if normalized is data:
        return str(source)
    destination = Path(tempfile.mkdtemp(prefix="pdf2zh-repaired-")) / source.name
    destination.write_bytes(normalized)
    return str(destination)


def install_pdf_input_fix():
    from pdf2zh import high_level

    original_translate_stream = high_level.translate_stream

    @functools.wraps(original_translate_stream)
    def translate_stream(stream, *args, **kwargs):
        source = normalize_pdf_bytes(stream)
        pages = kwargs.get('pages')
        if pages is not None:
            if not pages:
                raise ValueError("页码范围没有选中页面，请检查起始页是否大于结束页。")
            with pymupdf.open(stream=source, filetype='pdf') as document:
                page_count = len(document)
            if any(page < 0 or page >= page_count for page in pages):
                raise ValueError(f"这份 PDF 有 {page_count} 页，请填写 1 到 {page_count} 范围内的页码。")
        mono, dual = original_translate_stream(source, *args, **kwargs)
        cancellation_event = kwargs.get('cancellation_event')
        if cancellation_event and cancellation_event.is_set():
            raise CancelledError("task cancelled")
        from image_text import translate_image_text
        from layout_fixes import TRANSLATION_RESOURCES
        from pdf2zh.doclayout import ModelInstance

        translator, font = TRANSLATION_RESOURCES.get()
        if translator.lang_in not in ('en', 'auto', ''):
            return mono, dual
        updated = translate_image_text(
            source, mono, translator, kwargs.get('model') or ModelInstance.value,
            font, kwargs.get('thread', 4), pages=pages,
            cancellation_event=cancellation_event,
            callback=kwargs.get('callback'),
        )
        if cancellation_event and cancellation_event.is_set():
            raise CancelledError("task cancelled")
        if updated is mono:
            return mono, dual
        with pymupdf.open(stream=source, filetype='pdf') as original, pymupdf.open(stream=updated, filetype='pdf') as translated, pymupdf.open() as bilingual:
            for index in range(len(original)):
                bilingual.insert_pdf(original, from_page=index, to_page=index)
                bilingual.insert_pdf(translated, from_page=index, to_page=index)
            return updated, bilingual.tobytes(deflate=True, garbage=3)

    high_level.translate_stream = translate_stream

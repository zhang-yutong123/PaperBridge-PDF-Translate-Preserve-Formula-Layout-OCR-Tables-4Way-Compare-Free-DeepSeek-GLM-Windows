"""Translate prose embedded in PDF images without replacing native page content.

OCR grouping and bounded HTML layout follow PDFMathTranslate's experimental OCR
implementation (vendor/pdf2zh_ocr.py). Inline mathematical glyphs are copied as
small source-image fragments, so Tesseract's guessed formula spelling is never
written into the translated PDF.
"""

import asyncio
import logging
import math
import re
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pymupdf

from vendor.pdf2zh_ocr import ocr_paragraphs
from pdf_text import translated_html


LOG = logging.getLogger(__name__)
TESSDATA = Path(__file__).parent / "assets" / "tessdata"
WORDS = re.compile(r"[A-Za-z]{2,}")
PROSE = re.compile(r"^[A-Za-z]+(?:['’-][A-Za-z]+)*[,.!?:;]?$", re.ASCII)
COMMON = {"the", "a", "an", "of", "to", "is", "are", "and", "for", "that", "in", "with", "given", "where", "this", "as", "be", "we"}
SHORT_ENGLISH = {"an", "as", "at", "be", "by", "do", "go", "he", "if", "in", "is", "it", "me", "my", "no", "of", "on", "or", "so", "to", "up", "us", "we"}
PLACEHOLDER = re.compile(r"\{\s*v(\d+)\s*\}")


def _center_inside(word, rectangle):
    return pymupdf.Point((word[0] + word[2]) / 2, (word[1] + word[3]) / 2) in rectangle


def _is_sentence(words):
    text = " ".join(w[4] for w in words)
    tokens = WORDS.findall(text.lower())
    return len(tokens) >= 12 and sum(w in COMMON for w in tokens) >= 3


class _ImageWords:
    """Page view restricting upstream grouping to image prose and its glyphs."""

    def __init__(self, page, words):
        self.page = page
        self.words = words

    def __getattr__(self, name):
        return getattr(self.page, name)

    def get_text(self, kind, **kwargs):
        if kind == "words":
            return self.words
        return self.page.get_text(kind, **kwargs)


def _word_is_math(text):
    stripped = text.strip("()[]{}.,!?:;’'")
    if len(stripped) >= 3 and stripped.isalpha() and not any(c.isdigit() for c in text):
        text = stripped
    if not PROSE.fullmatch(text):
        return True
    letters = text.rstrip(",.!?:;")
    if len(letters) == 1:
        return letters not in {"a", "I"}
    if len(letters) == 2 and letters.islower():
        return letters not in SHORT_ENGLISH
    if letters.isupper() and len(letters) >= 2:
        return True
    if letters.lower() in {"log", "argmax", "argmin", "exp", "sin", "cos"}:
        return True
    # Short mixed-case tokens (Rn, Ax, Cx) are common OCR readings of notation.
    if len(letters) == 2 and letters[0].isupper() and letters[1].islower():
        return letters not in {"In", "It", "Is", "To", "We", "On", "An", "As", "If", "Or", "At", "By", "Be", "No", "Do"}
    return bool(re.fullmatch(r"A[a-z]{2,4}", letters) and letters not in {"And", "Also", "Above", "After", "Any", "All", "About"})


def _join_continuations(paragraphs):
    merged = []
    for bounds, text, size, words in sorted(paragraphs, key=lambda p: (p[0].y0, p[0].x0)):
        starts_item = re.match(r"^\(?[a-zA-Z0-9]+[.)]\s|^\([a-z]\.\w+\)", text)
        if merged and not starts_item:
            previous = merged[-1]
            gap = bounds.y0 - previous[0].y1
            horizontal = min(bounds.x1, previous[0].x1) - max(bounds.x0, previous[0].x0)
            if -1 <= gap <= min(size, previous[2]) * 0.6 and horizontal > min(bounds.width, previous[0].width) * 0.5:
                previous[0] |= bounds
                previous[1] += " " + text
                previous[3].extend(words)
                continue
        merged.append([pymupdf.Rect(bounds), text, size, list(words)])
    return merged


def _prepare_paragraph(paragraph, ocr_words, source_page):
    bounds, _, size, source_rects = paragraph
    lookup = {tuple(pymupdf.Rect(w[:4])): w for w in ocr_words}
    words = [lookup[tuple(rect)] for rect in source_rects]
    flags = [_word_is_math(w[4]) for w in words]
    # OCR sometimes merges a single formula character with nearby prose (1and
    # becomes Land). Preserve its source pixels rather than translating that guess.
    for i, word in enumerate(words):
        if word[4].lower() == "where" and i + 1 < len(words) and words[i + 1][4].lower() in {"is", "are"}:
            # Missing lambda/mu between "where" and "is": retain those original
            # pixels along with the lead word rather than silently drop notation.
            flags[i] = True
        if len(word[4].strip(",.;:")) <= 4 and i and any(x in words[i - 1][4] for x in "=<>≤≥∈"):
            flags[i] = True
        if word[4].strip(",.;:") in {"a", "I"} and (
            (i and any(x in words[i - 1][4] for x in "=<>≤≥+−*/"))
            or (i + 1 < len(words) and any(x in words[i + 1][4] for x in "=<>≤≥+−*/"))
        ):
            flags[i] = True

    parts, fragments = [], []
    i = 0
    while i < len(words):
        if not flags[i]:
            word_text = words[i][4]
            if word_text.lower() == "marks]" and i and "[" in words[i - 1][4]:
                word_text = "points]"
            parts.append(word_text)
            i += 1
            continue
        rectangle = pymupdf.Rect(words[i][:4])
        j = i + 1
        while j < len(words) and flags[j] and abs((words[j][1] + words[j][3] - words[i][1] - words[i][3]) / 2) < size * 0.8:
            rectangle |= pymupdf.Rect(words[j][:4])
            j += 1
        # Tesseract's invisible-font widths can stop before the actual ink,
        # especially italic symbols. Use the next word's starting coordinate as
        # the right limit, not the OCR font's guessed advance width.
        formula_right = rectangle.x1
        right = rectangle.x1 + size * 0.65
        next_word_on_line = j < len(words) and abs((words[j][1] + words[j][3] - words[i][1] - words[i][3]) / 2) < size * 0.8
        if next_word_on_line:
            right = words[j][0] - 0.5
        rectangle = pymupdf.Rect(rectangle.x0 - 0.7, rectangle.y0 - 1.3, right, rectangle.y1 + 1.3) & source_page.rect
        pix = source_page.get_pixmap(matrix=pymupdf.Matrix(3, 3), clip=rectangle, colorspace=pymupdf.csRGB, alpha=False)
        pixels = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3)
        if next_word_on_line:
            # The next word's OCR x0 can already lie inside its first stroke.
            # Stop at the actual inter-word white gap, beyond the formula box,
            # so that stroke is not copied as an apparent extra math symbol.
            blank = np.flatnonzero(~np.any(pixels < 220, axis=(0, 2)))
            blank = blank[blank + pix.x >= formula_right * 3]
            gaps = np.split(blank, np.flatnonzero(np.diff(blank) > 1) + 1)
            gaps = [gap for gap in gaps if len(gap) >= 2]
            if gaps:
                pixels = pixels[:, :gaps[-1][-1] + 1]
        ys, xs = np.where(np.any(pixels < 220, axis=2))
        if len(xs):
            rectangle = pymupdf.Rect((pix.x + xs.min()) / 3, (pix.y + ys.min()) / 3, (pix.x + xs.max() + 1) / 3, (pix.y + ys.max() + 1) / 3)
        png = source_page.get_pixmap(matrix=pymupdf.Matrix(3, 3), clip=rectangle, alpha=False).tobytes("png")
        fragments.append((png, rectangle.width, rectangle.height))
        parts.append("{v" + str(len(fragments) - 1) + "}")
        i = j
    return bounds, " ".join(parts), size, source_rects, fragments


def _restore_table_rules(source_page, target_page, table_rects):
    """Replay long table rules after the OCR text masks, using source pixels."""
    zoom = 3
    for table in table_rects:
        clip = (table + (-2, -2, 2, 2)) & source_page.rect
        pix = source_page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip, colorspace=pymupdf.csRGB, alpha=False)
        pixels = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3)
        ink = np.min(pixels, axis=2) < 200
        for axis in (0, 1):
            coverage = ink.mean(axis=1 if axis == 0 else 0)
            indices = np.flatnonzero(coverage > 0.65)
            if not len(indices):
                continue
            groups = np.split(indices, np.flatnonzero(np.diff(indices) > 1) + 1)
            for group in groups:
                if axis == 0:
                    rule = pymupdf.Rect(pix.x / zoom, (pix.y + group[0] - 1) / zoom, (pix.x + pix.width) / zoom, (pix.y + group[-1] + 2) / zoom)
                else:
                    rule = pymupdf.Rect((pix.x + group[0] - 1) / zoom, pix.y / zoom, (pix.x + group[-1] + 2) / zoom, (pix.y + pix.height) / zoom)
                rule &= source_page.rect
                strip = source_page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=rule, colorspace=pymupdf.csRGB, alpha=False)
                target_page.insert_image(rule, stream=strip.tobytes("png"))


def _render_paragraphs(source_page, target_page, prepared, translations, font, table_rects=(), rotation=0):
    """Upstream-style bounded HTML overlay, with original inline math images."""
    with pymupdf.open() as overlay:
        page = overlay.new_page(width=source_page.rect.width, height=source_page.rect.height)
        archive = pymupdf.Archive((font.buffer, "ocr.ttf"))
        for index, ((bounds, _, size, rects, fragments), translated) in enumerate(zip(prepared, translations)):
            markers = {int(m.group(1)) for m in PLACEHOLDER.finditer(translated)}
            if markers != set(range(len(fragments))):
                raise RuntimeError("图片正文翻译未保留数学标记，请重试当前页。")
            # Whole paragraph masks remove italic side bearings that OCR word
            # boxes miss. Display equations are separate protected paragraphs.
            page.draw_rect(bounds + (-1.5, -1.5, size * 0.8, 1.5), color=None, fill=(1, 1, 1))
            # MuPDF 1.25 can underestimate a final inline image during HTML fit.
            # Start from a measured text-plus-image scale so the last symbol is
            # inside the rectangle before Story's final bounded layout pass.
            width = font.text_length(PLACEHOLDER.sub("", translated), fontsize=size) + sum(f[1] for f in fragments)
            lines = max(1, math.ceil(width / bounds.width))
            scale = min(1.0, math.sqrt(bounds.height / (lines * size * 1.3)))
            size *= scale
            html = translated_html(translated)
            for n, (png, width, height) in enumerate(fragments):
                name = f"p{index}-formula{n}.png"
                archive.add((png, name))
                tag = f'<img src="{name}" style="width:{width * scale}pt;height:{height * scale}pt;vertical-align:middle">'
                html = re.sub(r"\{\s*v" + str(n) + r"\s*\}", lambda m: tag, html)
            css = (
                "@font-face {font-family:ocr;src:url(ocr.ttf);}"
                f"body {{font-family:ocr;font-size:{size}pt;line-height:1.2;margin:0;}}"
                "p {margin:0;overflow-wrap:break-word;text-align:left;}"
            )
            spare, _ = page.insert_htmlbox(bounds, f"<p>{html}</p>", css=css, archive=archive, scale_low=0)
            if spare < 0:
                raise RuntimeError("图片正文翻译无法排入原文区域。")
        _restore_table_rules(source_page, page, table_rects)
        target_page.show_pdf_page(target_page.rect, overlay, 0, rotate=rotation)


@contextmanager
def _ocr_working_page(source_page):
    """Keep OCR, image bounds and formula crops in one upright coordinate space."""
    def recognize(page):
        pixmap = page.get_pixmap(dpi=300, colorspace=pymupdf.csRGB, alpha=False, annots=False)
        data = pixmap.pdfocr_tobytes(language="eng", tessdata=str(TESSDATA))
        with pymupdf.open(stream=data, filetype="pdf") as ocr:
            tokens = WORDS.findall(ocr[0].get_text().lower())
        return data, (sum(word in COMMON for word in tokens), len(tokens))

    rotation = source_page.rotation
    source_page.set_rotation(0)
    recognized, score = recognize(source_page)
    # Some files store upright pixels plus /Rotate; others store sideways pixels
    # which /Rotate corrects. For the latter, flatten the displayed orientation
    # into a temporary page rather than mixing rotated OCR and native image boxes.
    if rotation:
        with pymupdf.open() as displayed:
            displayed.insert_pdf(source_page.parent, from_page=source_page.number, to_page=source_page.number)
            candidate = displayed[0]
            candidate.set_rotation(rotation)
            candidate.remove_rotation()
            displayed_ocr, displayed_score = recognize(candidate)
            if displayed_score > score:
                yield candidate, displayed_ocr, rotation
                return
    yield source_page, recognized, 0


def translate_image_text(source_bytes, mono_bytes, translator, model, font, thread=4, *, pages=None, cancellation_event=None, callback=None):
    """Add translated image prose to mono PDF bytes; preserve source bytes.

    ``pages`` is an optional list of zero-based page numbers, matching pdf2zh.
    ``translator`` must expose translate(text); model is the loaded DocLayout
    model; font is a PyMuPDF Font. This handles English text in raster images.
    """
    with pymupdf.open(stream=source_bytes, filetype="pdf") as source, pymupdf.open(stream=mono_bytes, filetype="pdf") as mono:
        changed = False
        selected_pages = list(range(len(source))) if pages is None else list(pages)
        for number, source_page in enumerate(source):
            if pages is not None and number not in pages:
                continue
            if cancellation_event and cancellation_event.is_set():
                raise asyncio.CancelledError("task cancelled")
            if callback:
                callback(SimpleNamespace(n=selected_pages.index(number), total=len(selected_pages), desc="正在整理公式与页面文字"))
            # Native plots and raster content can coexist (for example a chart
            # beside a logo). Process each kind independently on mixed pages.
            from native_math import translate_dense_native_page
            dense = translate_dense_native_page(source_page, mono[number], translator, font, thread, cancellation_event)
            changed = changed or dense
            from structured_images import translate_native_charts, translate_native_tables
            regions = translate_native_tables(source_page, mono[number], translator, font, thread, cancellation_event)
            changed = changed or bool(regions)
            if not dense:
                regions = translate_native_charts(source_page, mono[number], translator, font, model, thread, cancellation_event)
                changed = changed or bool(regions)
            image_rects = [pymupdf.Rect(info["bbox"]) for info in source_page.get_image_info() if pymupdf.Rect(info["bbox"]).get_area() >= 1500]
            if not image_rects:
                continue
            if callback:
                callback(SimpleNamespace(n=selected_pages.index(number), total=len(selected_pages), desc="正在识别并翻译图片文字"))
            with _ocr_working_page(source_page) as (source_page, recognized, render_rotation):
                image_rects = [pymupdf.Rect(info["bbox"]) for info in source_page.get_image_info() if pymupdf.Rect(info["bbox"]).get_area() >= 1500]
                with pymupdf.open(stream=recognized, filetype="pdf") as ocr:
                    page = ocr[0]
                    words = page.get_text("words")
                    prose_rects = [rect for rect in image_rects if _is_sentence([w for w in words if _center_inside(w, rect)])]
                    pix = source_page.get_pixmap(colorspace=pymupdf.csRGB, alpha=False)
                    rgb = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3)
                    detection = model.predict(rgb[:, :, ::-1], imgsz=max(32, int(pix.height / 32) * 32))[0]
                    from structured_images import translate_structured_page
                    regions, handled = translate_structured_page(
                        source_page, mono[number], translator, font, detection, image_rects,
                        thread, render_rotation, cancellation_event, words,
                    )
                    changed = changed or bool(regions)
                    words = [word for word in words if not any(_center_inside(word, rect) for rect in handled)]
                    if not words:
                        continue
                    caption_rects = [pymupdf.Rect(*box.xyxy) for box in detection.boxes if detection.names[int(box.cls)] == "figure_caption" and any((pymupdf.Rect(*box.xyxy) & r).get_area() > 0 for r in image_rects)]
                    table_rects = [pymupdf.Rect(*box.xyxy) for box in detection.boxes if detection.names[int(box.cls)] == "table" and any((pymupdf.Rect(*box.xyxy) & r).get_area() > 0 for r in image_rects)]
                    table_words = [w for w in words if re.fullmatch(r"[A-Za-z]{3,}", w[4]) and any(_center_inside(w, r) for r in table_rects)]
                    # Group adjacent prose words into their own table cells. Numeric
                    # cells are not selected, masked, OCR-rewritten, or translated.
                    cells = []
                    for word in sorted(table_words, key=lambda w: (w[1], w[0])):
                        rect = pymupdf.Rect(word[:4])
                        if cells and abs(rect.y0 - cells[-1].y0) < 2 and 0 <= rect.x0 - cells[-1].x1 < rect.height * 1.5:
                            cells[-1] |= rect
                        else:
                            cells.append(rect)
                    prose_rects.extend(caption_rects)
                    if not prose_rects and not cells:
                        continue
                    selected = [w for w in words if any(_center_inside(w, rect) for rect in prose_rects) or w in table_words]
                    # A sentence-bearing raster block can itself be called "figure".
                    # Dedicated formula regions remain protected by upstream grouping.
                    detection.boxes = [box for box in detection.boxes if not (
                        detection.names[int(box.cls)] in {"figure", "table"}
                        and any((pymupdf.Rect(*box.xyxy) & rect).get_area() > 0 for rect in prose_rects + cells)
                    )]
                    text_class = next(i for i, name in (detection.names.items() if isinstance(detection.names, dict) else enumerate(detection.names)) if name == "plain text")
                    detection.boxes.extend(SimpleNamespace(xyxy=np.array(list(rect)), cls=text_class, conf=1.0) for rect in cells)
                    paragraphs = _join_continuations(ocr_paragraphs(_ImageWords(page, selected), detection))
                    paragraphs = [p for p in paragraphs if any(w.lower() in COMMON for w in WORDS.findall(p[1])) or len(WORDS.findall(p[1])) >= 4 or any((p[0] & cell).get_area() > 0 for cell in cells)]
                    prepared = [_prepare_paragraph(p, selected, source_page) for p in paragraphs]
                    if not prepared:
                        continue
                    with ThreadPoolExecutor(max_workers=max(1, thread)) as executor:
                        translations = list(executor.map(lambda p: translator.translate(p[1]), prepared))
                    target_page = mono[number]
                    rotation = target_page.rotation
                    target_page.set_rotation(0)
                    try:
                        _render_paragraphs(source_page, target_page, prepared, translations, font, table_rects, render_rotation)
                    finally:
                        target_page.set_rotation(rotation)
                    changed = True
                    LOG.info("Translated image prose on page %d: %d paragraphs", number + 1, len(prepared))
        if not changed:
            return mono_bytes
        # HTML overlays embed the complete CJK font. Retain only glyphs used in
        # the finished document before serialization, as in pdf2zh's main output.
        mono.subset_fonts()
        return mono.tobytes(deflate=True, garbage=3)

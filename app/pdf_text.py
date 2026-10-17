"""HTML text with searchable Latin identifiers alongside the translated font."""
from html import escape
import re


def translated_html(text):
    # Source Han Serif's alternate Latin-number glyphs in MuPDF Story display
    # correctly but receive incorrect Unicode mappings (e.g. CS5489). Keep
    # Latin runs in Story's built-in font and retain the chosen CJK typeface.
    return ''.join(
        f'<span style="font-family:sans-serif">{escape(part)}</span>'
        if re.fullmatch(r'[\x20-\x7e]+', part) else escape(part)
        for part in re.split(r'([\x20-\x7e]+)', text)
        if part
    )

"""Preserve numeric dash arrays in the classic engine's PDF stream output."""

from decimal import Decimal
import functools
import re


_DASH_ARRAY = re.compile(
    r"\[\s*(?P<values>[+\-\d.eE\s,]+)\s*\]"
    r"(?P<suffix>\s+[+\-]?(?:\d+(?:\.\d*)?|\.\d+)\s+d\b)"
)


def _repair_dash_arrays(operations):
    if not operations:
        return operations

    def replace(match):
        values = match.group('values')
        if ',' not in values and not re.search(r'[eE]', values):
            return match.group(0)
        # Python list repr adds commas; PDF number arrays require spaces.
        # Decimal also converts Python's exponent notation to PDF decimals.
        values = ' '.join(format(Decimal(item), 'f') for item in re.split(r'[,\s]+', values.strip()))
        return '[' + values + ']' + match.group('suffix')

    return _DASH_ARRAY.sub(replace, operations)


def install_vector_fixes():
    """Keep upstream rendering and repair only emitted dash-array syntax."""
    from pdf2zh.pdfinterp import PDFPageInterpreterEx

    original_execute = PDFPageInterpreterEx.execute
    if getattr(original_execute, '_desktop_dash_array_fix', False):
        return

    @functools.wraps(original_execute)
    def execute(self, streams):
        return _repair_dash_arrays(original_execute(self, streams))

    execute._desktop_dash_array_fix = True
    PDFPageInterpreterEx.execute = execute

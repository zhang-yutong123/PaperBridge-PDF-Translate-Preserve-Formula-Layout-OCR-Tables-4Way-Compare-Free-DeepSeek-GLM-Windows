"""Small adapters for the classic engine's prose fonts and list paragraphs."""

import functools
import re
from contextvars import ContextVar

from pdfminer.layout import LTChar, LTLine

TRANSLATION_RESOURCES = ContextVar('pdf2zh_translation_resources')


# Use the upstream vfont option; ordinary Italic prose is not a math font.
PROSE_VFONT = r"(CM[^R]|MS.M|XY|MT|BL|RM|EU|LA|RS|LINE|LCIRCLE|TeX-|rsfs|txsy|wasy|stmary|.*Mono|.*Code|.*Sym|.*Math|.*Wingdings|.*Dingbats)"
LIST_PREFIX = re.compile(r"^\s*(?:\d{1,3}[.):]|[A-Za-z][.)]|[ivxlcdm]{2,5}[.)]|\([A-Za-z0-9]+(?:\.\d+)*\)|[•●▪◦‣⁃❖\uf076])\s+")


def text_rows(page, include_scripts=False):
    characters = [c for c in page if isinstance(c, LTChar) and c.get_text()]
    position = (lambda c: c.y0) if include_scripts else (lambda c: c.matrix[5])
    characters.sort(key=lambda c: (-position(c), c.x0))
    rows = []
    for char in characters:
        baseline = position(char)
        tolerance = .65 if include_scripts else .2
        if not rows or abs(position(rows[-1][0]) - baseline) > max(.8, char.size * tolerance):
            rows.append([])
        rows[-1].append(char)
    for row in rows:
        row.sort(key=lambda c: c.x0)
        segment = []
        for char in row:
            if segment and char.x0 - segment[-1].x1 > 5 * char.size:
                yield segment
                segment = []
            segment.append(char)
        if segment:
            yield segment


def protect_inline_math(page):
    functions = re.compile(r'(?:argmax|argmin|min|max|log|sin|cos|tan|exp|lim|ln|Union|Find)')
    math_glyphs = [char for char in page if isinstance(char,LTChar) and (
        re.search(r'Math|CM[^R]|MS.M|.*Sym',str(char.fontname))
        or (re.search(r'CMR',str(char.fontname)) and char.get_text() in '()[]{}=+')
        or re.search(r'[=+−∑∏∫∥|≤≥⩽⩾∈∼⊤]',char.get_text()))]
    for row in text_rows(page, include_scripts=True):
        text, offsets = '', []
        previous = None
        for char in row:
            if previous and char.x0 - previous.x1 > .25 * char.size:
                text += ' '
                offsets.append(None)
            text += char.get_text()
            offsets.extend([char] * len(char.get_text()))
            previous = char
        spans = []
        # LaTeX's operator names often use the same Roman font as the prose.
        # Preserve an operator when its immediate neighbors are mathematical;
        # ordinary phrases such as "max variance" remain translatable.
        math_letters = set()
        for match in re.finditer(r'[A-Za-z]+', text):
            glyphs = [char for char in offsets[match.start():match.end()] if char]
            if not glyphs or not all('Ital' in str(char.fontname) for char in glyphs):
                continue
            token = match.group()
            sizes = [char.size for char in glyphs]
            if len(token) == 1 or (token.isupper() and len(token) <= 3) or min(sizes)<max(sizes)*.8:
                math_letters.update(range(match.start(),match.end()))
        def mathematical_neighbor(start,end):
            glyphs = [char for char in offsets[start:end] if char]
            if not glyphs:
                return False
            left,right = min(c.x0 for c in glyphs),max(c.x1 for c in glyphs)
            size = max(c.size for c in glyphs)
            in_row = any(char is not None and not start <= index < end
                       and max(left-char.x1,char.x0-right,0) < size*1.3
                       and (index in math_letters or re.search(r'[=+−∑∏∫∥|≤≥⩽⩾∈∼⊤]',char.get_text())
                            or (re.search(r'CMR|Math|DesktopMath',str(char.fontname))
                                and char.get_text() in '()[]{}'))
                       for index,char in enumerate(offsets))
            # Fraction numerators and operator subscripts may be on a separate
            # baseline, so also inspect nearby source math glyphs geometrically.
            bottom,top = min(c.y0 for c in glyphs),max(c.y1 for c in glyphs)
            return in_row or any(max(left-c.x1,c.x0-right,0)<size*1.3
                                 and max(bottom-c.y1,c.y0-top,0)<size*.65
                                 for c in math_glyphs)
        for match in re.finditer(r'(?<![A-Za-z])(?:arg\s*(?:max|min)|min|max|log|sin|cos|tan|exp|lim|ln)(?![A-Za-z])',text):
            if match.group().startswith('arg') or mathematical_neighbor(*match.span()):
                spans.append(match.span())
        for match in re.finditer(r'[A-Za-z]+',text):
            if all(index in math_letters for index in range(*match.span())):
                if match.group() not in {'a','A','I'} or mathematical_neighbor(*match.span()):
                    spans.append(match.span())
        for match in re.finditer(r'\d+(?:\.\d+)?\s*[·ꞏ×]\s*\d+(?:\.\d+)?', text):
            spans.append(match.span())
        for match in re.finditer(r'\[[^\]]+\]|\S+', text):
            token = match.group().rstrip('.,;:')
            remainder = functions.sub('', token)
            math_syntax = re.search(r'[+−=×ꞏ*/^_≤≥⩽⩾∈∼⊤|()]|\w-\w|\bO\(|^\[', token)
            no_prose = not re.search(r'[A-Za-z]{3,}', remainder)
            glyphs = [char for char in offsets[match.start():match.start()+len(token)] if char]
            sizes = [char.size for char in glyphs]
            attached_script = len(sizes) > 1 and min(sizes) < max(sizes) * .8 and no_prose
            short_italic = token.isalpha() and len(token) == 1 and token not in {'a', 'A', 'I'} and any('Ital' in str(char.fontname) for char in glyphs)
            if (math_syntax and no_prose) or attached_script or short_italic:
                spans.append((match.start(), match.start()+len(token)))
        for start, end in spans:
            for char in offsets[start:end]:
                if char is not None:
                    char.fontname = 'DesktopMath'


def split_list_regions(page, mask):
    height, width = mask.shape

    def location(char):
        return min(height - 1, max(0, int(char.y0))), min(width - 1, max(0, int(char.x0)))

    next_label = int(mask.max()) + 1
    active = []
    for row in text_rows(page, include_scripts=True):
        visible = [char for char in row if char.get_text().strip()]
        if not visible:
            continue
        words = []
        previous = None
        for char in row:
            if previous and char.x0 - previous.x1 > .25 * char.size:
                words.append(' ')
            words.append(char.get_text())
            previous = char
        text = ''.join(words).strip()
        classes = {int(mask[location(char)]) for char in visible} - {0}
        x0, baseline, size = visible[0].x0, visible[0].matrix[5], visible[0].size
        active = [item for item in active if item['baseline'] - baseline < 2.2 * size]
        prefix = LIST_PREFIX.match(text)
        promote_heading = prefix and not classes and len(re.findall(r'\b[A-Za-z]{3,}\b', text)) >= 2
        item = None
        if prefix and (classes or promote_heading):
            item = {'label': next_label, 'x0': x0, 'baseline': baseline, 'classes': classes}
            next_label += 1
            active = [old for old in active if abs(old['x0'] - x0) > size]
            active.append(item)
        else:
            for candidate in active:
                if (candidate['classes'] & classes and x0 > candidate['x0'] + .4 * size
                        and 0 < candidate['baseline'] - baseline < 2.2 * size):
                    item = candidate
                    item['baseline'] = baseline
                    break
        if item:
            for char in row:
                point = location(char)
                if mask[point] != 0 or promote_heading:
                    mask[point] = item['label']


def protect_display_math(page,mask):
    """Keep fraction-rich mathematical rows in their original coordinate frame.

    Font protection alone leaves a formula inside a flowing prose paragraph.
    Class zero tells the classic engine to replay the source glyphs and rules
    at their original positions, including numerators and underbraces.
    """
    height,width=mask.shape
    rules=[line for line in page if isinstance(line,LTLine)
           and line.width>4 and line.height<1 and line.linewidth<2]
    if not rules:
        return
    for row in text_rows(page,include_scripts=True):
        segments=[]
        for char in row:
            if not char.get_text().strip():
                continue
            label=int(mask[min(height-1,max(0,int(char.y0))),min(width-1,max(0,int(char.x0)))])
            if (not segments or label!=segments[-1][0]
                    or char.x0-segments[-1][1][-1].x1>char.size*2):
                segments.append((label,[]))
            segments[-1][1].append(char)
        for _,glyphs in segments:
            size=max(char.size for char in glyphs)
            left,right=min(c.x0 for c in glyphs),max(c.x1 for c in glyphs)
            bottom,top=min(c.y0 for c in glyphs),max(c.y1 for c in glyphs)
            nearby=[line for line in rules if line.x1>=left-size*16 and line.x0<=right+size*16
                    and bottom-size*3.5<=line.y0<=top+size*3.5]
            if not nearby:
                continue
            mathematical=[bool(re.search(PROSE_VFONT,str(c.fontname))) or c.get_text().isdigit()
                          for c in glyphs]
            prose=''.join(' ' if mathematical[i] else c.get_text() for i,c in enumerate(glyphs))
            if sum(mathematical)<len(glyphs)*.35 or len(re.findall(r'[A-Za-z]{3,}',prose))>1:
                continue
            x0,x1=max(0,int(left)-1),min(width,int(right)+2)
            y0,y1=max(0,int(bottom)-1),min(height,int(top)+2)
            mask[y0:y1,x0:x1]=0
            for line in nearby:
                if line.x0>=left-size and line.x1<=right+size:
                    y=min(height-1,max(0,int(line.y0)))
                    mask[y,max(0,int(line.x0)):min(width,int(line.x1)+1)]=0


def install_layout_fixes():
    from pdf2zh.converter import TranslateConverter

    original_init = TranslateConverter.__init__
    original_receive = TranslateConverter.receive_layout

    @functools.wraps(original_init)
    def initialize(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        TRANSLATION_RESOURCES.set((self.translator, self.noto))
        if not self.vfont:
            self.vfont = PROSE_VFONT

    @functools.wraps(original_receive)
    def receive_layout(self, page):
        split_list_regions(page, self.layout[page.pageid])
        if self.vfont == PROSE_VFONT:
            protect_inline_math(page)
            protect_display_math(page,self.layout[page.pageid])
        return original_receive(self, page)

    TranslateConverter.__init__ = initialize
    TranslateConverter.receive_layout = receive_layout

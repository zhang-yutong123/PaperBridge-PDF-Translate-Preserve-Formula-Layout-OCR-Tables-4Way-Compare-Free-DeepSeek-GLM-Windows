"""Region-aware OCR for tables, slide text and chart labels.

RapidOCR supplies text lines from PaddleOCR's English recognition model. Table
rules and text-region ownership constrain grouping and every rendered overlay.
Mathematical scan paragraphs remain with image_text's source-glyph renderer.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
import re
import threading

import cv2
import numpy as np
import pymupdf
from rapidocr_onnxruntime import RapidOCR
from pdf_text import translated_html

ASSETS = Path(__file__).parent / 'assets'
_ENGINE = None
_OCR_LOCK = threading.Lock()
ML_ABBREVIATIONS = frozenset('MLE MAP LS LR SVM KSVM PCA LSA FA CCA KDE GMM EM RF SGD CNN CNNs AE MAE VAE GAN RNN LLM'.split())


@dataclass
class TextLine:
    rect: pymupdf.Rect
    text: str
    score: float = 1.0
    rotation: int = 0


@dataclass
class TextRegion:
    rect: pymupdf.Rect
    lines: list[TextLine]
    kind: str
    size: float
    align: str = 'left'
    text: str = ''
    translated: str = ''
    color: tuple = (0, 0, 0)
    rotation: int = 0
    machine_learning: bool = False
    geometric: bool = False


def _center(rect):
    return (rect.tl + rect.br) / 2


def _union(rects):
    result = pymupdf.Rect(rects[0])
    for rect in rects[1:]:
        result |= rect
    return result


def read_lines(page, bounds=None, scale=2.0):
    global _ENGINE
    pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=bounds, colorspace=pymupdf.csRGB, alpha=False)
    rgb = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3)
    with _OCR_LOCK:
        if _ENGINE is None:
            _ENGINE = RapidOCR(rec_model_path=str(ASSETS / 'en_PP-OCRv4_rec_mobile.onnx'),
                det_limit_side_len=1600, det_limit_type='max', intra_op_num_threads=4, inter_op_num_threads=1)
        # Page orientation has already been resolved by _ocr_working_page.
        # Reclassifying tiny labels as upside-down turns "Tut 6" into "9 n".
        result, _ = _ENGINE(rgb[:, :, ::-1],use_cls=False)
    lines = []
    for box, text, score in result or []:
        points = (np.asarray(box) + (pix.x, pix.y)) / scale
        rect = pymupdf.Rect(points[:, 0].min(), points[:, 1].min(), points[:, 0].max(), points[:, 1].max())
        rotation = 90 if rect.height > rect.width * 1.5 else 0
        if rotation:
            # The detector's perspective crop can distort vertical axis labels.
            # Recognize a higher-resolution horizontal crop in both directions.
            crop = page.get_pixmap(matrix=pymupdf.Matrix(4,4),clip=rect+(-2,-2,2,2),colorspace=pymupdf.csRGB,alpha=False)
            pixels = np.frombuffer(crop.samples,np.uint8).reshape(crop.height,crop.width,3)
            candidates = []
            with _OCR_LOCK:
                for direction,angle in ((cv2.ROTATE_90_CLOCKWISE,90),(cv2.ROTATE_90_COUNTERCLOCKWISE,270)):
                    recognized,_ = _ENGINE(cv2.rotate(pixels,direction)[:,:,::-1],use_det=False,use_cls=False)
                    if recognized:
                        candidates.append((recognized[0][1],recognized[0][0],angle))
            if candidates:
                score,text,rotation = max(candidates)
        lines.append(TextLine(rect, text.strip(), float(score), rotation))
    return lines, rgb, scale


def _clusters(indices):
    if len(indices) == 0:
        return []
    return [float(np.mean(group)) for group in np.split(indices, np.flatnonzero(np.diff(indices) > 1) + 1)]


def table_grid(rgb, scale, bounds):
    """Recover ruling lines; a table cell is an actual geometric enclosure."""
    x0, y0, x1, y1 = (int(v * scale) for v in bounds)
    crop = rgb[max(0,y0):y1, max(0,x0):x1]
    gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
    ink = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY_INV)[1]
    horizontal = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((1, max(20,crop.shape[1]//5)),np.uint8))
    vertical = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((max(20,crop.shape[0]//4),1),np.uint8))
    ys = _clusters(np.flatnonzero(np.mean(horizontal > 0, axis=1) > .55))
    xs = _clusters(np.flatnonzero(np.mean(vertical > 0, axis=0) > .55))
    if len(xs) < 3 or len(ys) < 4:
        return []
    return [pymupdf.Rect((x0+a)/scale, (y0+c)/scale, (x0+b)/scale, (y0+d)/scale)
            for c,d in zip(ys,ys[1:]) for a,b in zip(xs,xs[1:])]


def _join_lines(lines):
    ordered = sorted(lines, key=lambda line: (round(line.rect.y0/3),line.rect.x0))
    text = ' '.join(line.text for line in ordered)
    return re.sub(r'\s+', ' ', text).strip()


def _font_size(lines):
    return float(np.median([min(line.rect.height,line.rect.width)*.82 for line in lines]))


def _has_words(text):
    return bool(re.search(r'[A-Za-z]{2,}',text))


def _reference_code(text):
    words = re.findall(r'[A-Za-z]+', text)
    reference_words = ML_ABBREVIATIONS | {'pPCA','PRML','DHS','MG','H','B','GBC','Appendix','C','Ch','pg','PA','A','CS'}
    return bool(words) and all(word in reference_words for word in words)


def _comparison_cells(lines,bounds):
    """Infer cells in text-keyed comparison tables without vertical rules."""
    if not lines:
        return None
    size=float(np.median([line.rect.height for line in lines]))
    first=min(_center(line.rect).y for line in lines)
    headers=sorted([line for line in lines if abs(_center(line.rect).y-first)<size*.55],key=lambda line:line.rect.x0)
    if len(headers)<3:
        return None
    keys=sorted([line for line in lines if _center(line.rect).x<headers[1].rect.x0
                 and _center(line.rect).y>first+size*.6 and _has_words(line.text)],key=lambda line:_center(line.rect).y)
    if len(keys)<3:
        return None
    centers=[float(np.mean([_center(line.rect).y for line in headers]))]+[_center(line.rect).y for line in keys]
    ys=[bounds.y0]+[(a+b)/2 for a,b in zip(centers,centers[1:])]+[bounds.y1]
    xs=[headers[0].rect.x0-.3]+[header.rect.x0-.5 for header in headers[1:]]+[bounds.x1]
    font_size=min(_font_size(lines),float(np.percentile(np.diff(centers),25))*.65)
    regions=[]
    for y0,y1 in zip(ys,ys[1:]):
        for x0,x1 in zip(xs,xs[1:]):
            cell=pymupdf.Rect(x0,y0,x1,y1)
            content=[line for line in lines if _center(line.rect) in cell]
            text=_join_lines(content)
            if not _has_words(text) or _reference_code(text):
                continue
            ink=_union([line.rect for line in content])
            box=pymupdf.Rect(cell.x0+.3,max(bounds.y0,ink.y0-.4),cell.x1-.3,min(bounds.y1,ink.y1+.4))
            regions.append(TextRegion(box,content,'table',font_size,text=text))
    return regions


def table_regions(lines, bounds, rgb, scale):
    cells = table_grid(rgb,scale,bounds)
    regions = []
    if cells:
        reference_cells=[cell for cell in cells if any(_center(line.rect) in cell and re.fullmatch(r'References?',line.text,re.I) for line in lines)]
        for cell in cells:
            if any(abs(cell.x0-header.x0)<1 and abs(cell.x1-header.x1)<1 and cell.y0>=header.y1-1 for header in reference_cells):
                continue
            content = [line for line in lines if _center(line.rect) in cell]
            if not content:
                continue
            text = _join_lines(content)
            if not _has_words(text) or _reference_code(text):
                continue
            inset = cell + (1.5,1.0,-1.5,-1.0)
            regions.append(TextRegion(inset,content,'table',_font_size(content),text=text))
        size=float(np.median([r.size for r in regions])) if regions else 10
        for region in regions:
            region.size=size
        return regions

    comparison=_comparison_cells(lines,bounds)
    if comparison is not None:
        return comparison

    # Borderless tables: a detected text line is independent from the rows above
    # and below. Extend only into free horizontal space on that same row.
    reference_bands=[]
    for header in lines:
        if not re.fullmatch(r'References?',header.text,re.I):
            continue
        subheaders=[line for line in lines if header.rect.y1-2<=line.rect.y0<=header.rect.y1+header.rect.height*1.5
                    and header.rect.x0-header.rect.width*.6<=_center(line.rect).x<=header.rect.x1+header.rect.width*.6
                    and line.rect.height<header.rect.height*.85]
        band=_union([line.rect for line in subheaders]) if subheaders else header.rect
        reference_bands.append(pymupdf.Rect(band.x0-2,header.rect.y1,band.x1+2,bounds.y1))
    ordered = sorted(lines,key=lambda line:(line.rect.y0,line.rect.x0))
    consumed = set()
    for index,line in enumerate(ordered):
        if index in consumed or not _has_words(line.text) or _reference_code(line.text):
            continue
        if any(_center(line.rect) in band for band in reference_bands):
            continue
        content = [line]
        text = line.text
        # A header split through a word (e.g. "Assessm" / "ent") is one label.
        for j,following in enumerate(ordered[index+1:],index+1):
            if (line.rect.y1 <= following.rect.y0 <= line.rect.y1 + line.rect.height*.5
                and abs(following.rect.x0-line.rect.x0) < line.rect.height
                and re.fullmatch('[a-z]{1,4}',following.text)
                and line.rect.y0 < bounds.y0 + bounds.height*.12):
                content.append(following)
                text += following.text
                consumed.add(j)
                break
        box = _union([l.rect for l in content])
        neighbors = [other.rect.x0 for other in lines if other not in content
                     and other.rect.x0 >= box.x1-1
                     and min(other.rect.y1,box.y1)-max(other.rect.y0,box.y0) > min(other.rect.height,box.height)*.3]
        right = min(neighbors,default=bounds.x1)-2
        box.x1 = max(box.x1,min(right,box.x1+max(5,box.height)))
        box = (box + (-.5,-.5,.5,.5)) & bounds
        regions.append(TextRegion(box,content,'table',_font_size(content),text=text))
    size=float(np.median([r.size for r in regions])) if regions else 10
    for region in regions:
        region.size=size
    return regions


def is_chart(lines, bounds, rgb, scale):
    """Axes/tick evidence separates data plots from covers and illustrations."""
    numeric = [line for line in lines if re.fullmatch(r'[+−\-]?\d+(?:[.,]\d+)?%?',line.text.strip())]
    if sum('%' in line.text for line in numeric)>=2:
        return True
    if len(numeric) < 3:
        return False
    aligned_x = max((sum(abs(a.rect.x0-b.rect.x0) < max(3,a.rect.height) for b in numeric) for a in numeric),default=0)
    aligned_y = max((sum(abs(a.rect.y0-b.rect.y0) < max(3,a.rect.height*.65) for b in numeric) for a in numeric),default=0)
    category_labels=sum(_has_words(line.text) and line.rect.y0>bounds.y0+bounds.height*.7 for line in lines)
    return (aligned_x >= 3 and aligned_y >= 3) or (aligned_x >= 3 and category_labels >= 2)


def build_regions(page, detection, image_rects, lines, rgb, scale):
    """Return translation units plus areas excluded from the legacy math OCR."""
    boxes = [(pymupdf.Rect(*b.xyxy), detection.names[int(b.cls)], float(b.conf)) for b in detection.boxes]
    native = page.get_text('words')
    lines = [line for line in lines if any(_center(line.rect) in rect for rect in image_rects)
             and not any(_center(pymupdf.Rect(word[:4])) in line.rect for word in native)]
    ignored = [rect for rect,kind,_ in boxes if kind in {'abandon','isolate_formula','formula_caption','table_footnote'}]
    lines=[line for line in lines if not any(_center(line.rect) in rect for rect in ignored)]
    tables = [rect for rect,kind,_ in boxes if kind == 'table']
    figures = [rect for rect,kind,_ in boxes if kind == 'figure']
    # Use maximal figure rectangles once; nested detections describe the same art.
    figures = [rect for rect in figures if not any(rect != other and (rect & other).get_area() >= rect.get_area()*.97 for other in figures)]
    # A formula/prose scan can be labelled as one large figure. Keep that with
    # the source-glyph OCR route; actual decorative figures stay protected.
    math_boxes=[rect for rect,kind,_ in boxes if kind=='isolate_formula']
    figures=[rect for rect in figures if not any((rect & formula).get_area()>formula.get_area()*.5 for formula in math_boxes)]
    handled = list(tables) + list(figures) + list(ignored)
    regions = []
    for table in tables:
        table_lines = [line for line in lines if _center(line.rect) in table]
        regions.extend(table_regions(table_lines,table+(-2,-2,2,2),rgb,scale))
    for figure in figures:
        content = [line for line in lines if _center(line.rect) in figure]
        if is_chart(content,figure,rgb,scale):
            for line in content:
                if _has_words(line.text):
                    box = (line.rect + (-1,-1,1,1)) & figure
                    regions.append(TextRegion(box,[line],'chart',_font_size([line]),text=line.text,rotation=line.rotation))

    has_formula = any(kind == 'isolate_formula' for _,kind,_ in boxes) or any(
        re.search(r'[=∑Σ∏∫∞≤≥±]|\barg\s*(?:max|min)\b|\blog\s*p\s*\(',line.text,re.I)
        for line in lines if not any(_center(line.rect) in rect for rect in tables+figures))
    bibliography = any(re.search(r'\b(?:References|Textbooks|Learning\s+Resources)\b',line.text,re.I) for line in lines)
    text_boxes = [(rect,kind,score) for rect,kind,score in boxes if kind in {'title','plain text','figure_caption','table_caption','table_footnote'}]
    groups = {}
    for line in lines:
        if not _has_words(line.text) or any(_center(line.rect) in rect for rect in handled+ignored):
            continue
        matching = [(i,rect,kind,score) for i,(rect,kind,score) in enumerate(text_boxes) if _center(line.rect) in rect]
        if matching:
            i,owner,kind,_ = min(matching,key=lambda item:item[1].get_area())
            if kind == 'plain text' and has_formula and not bibliography:
                continue
        else:
            i,owner,kind = ('line',len(groups)),line.rect,'plain text'
            if has_formula and not bibliography:
                continue
        groups.setdefault((i,kind),[]).append(line)

    for (identifier,kind),content in groups.items():
        owner = text_boxes[identifier][0] if isinstance(identifier,int) else content[0].rect
        chunks = []
        for line in sorted(content,key=lambda item:(item.rect.y0,item.rect.x0)):
            if chunks:
                prev=chunks[-1][-1]
                same_row=abs(_center(line.rect).y-_center(prev.rect).y) < min(line.rect.height,prev.rect.height)*.4
                continuation=(0 <= line.rect.y0-prev.rect.y1 < min(line.rect.height,prev.rect.height)*.55
                              and abs(line.rect.x0-prev.rect.x0)<line.rect.height
                              and re.match(r'^[a-z]',line.text))
                if same_row or continuation:
                    chunks[-1].append(line)
                    continue
            chunks.append([line])
        for chunk in chunks:
            box=_union([line.rect for line in chunk])
            text=_join_lines(chunk)
            actual_kind='bibliography' if bibliography and kind=='plain text' and re.match(r'^[A-Z]',text) else kind
            padded=(box+(-1,-1,1,1)) & page.rect
            regions.append(TextRegion(padded,chunk,actual_kind,_font_size(chunk),
                align='center' if kind=='title' else 'left',text=text))
            handled.append(padded)
    context=' '.join(line.text for line in lines)
    machine_learning=bool(re.search(r'machine learning|supervised learning|neural networks?|training|Bayes|regression',context,re.I))
    for region in regions:
        region.machine_learning=machine_learning
    return regions,handled


def _expand_labels(text,kind):
    if kind not in {'table','native_table'}:
        return text
    text=re.sub(r'\bLec\s*(\d+)\s*:',r'Lecture \1:',text,flags=re.I)
    text=re.sub(r'\bTut\s*(\d+)\b',r'Tutorial class \1',text,flags=re.I)
    text=re.sub(r'\bPr\.\s*Pres\.?', 'Project presentations',text,flags=re.I)
    text=re.sub(r'\bPr\.\s*Due', 'Project deadline',text,flags=re.I)
    text=re.sub(r'\b(?:Pr\.|Project)\s*out\b','Project released',text,flags=re.I)
    text=re.sub(r'\b(PA\d+|A\d+)\s+out\b',r'\1 released',text)
    text=re.sub(r'\b(PA\d+|A\d+)\s+due\b',r'\1 deadline',text)
    return {'Wk':'Week','Tutorial':'Tutorial class'}.get(text,text)


def translate_region(region,translator):
    text=_expand_labels(region.text,region.kind)
    if region.kind in {'table','native_table'} and translator.lang_out in {'zh','zh-CN','zh-Hans'}:
        tutorial=re.fullmatch(r'Tutorial class(?:\s+(\d+))?',text,re.I)
        if tutorial:
            return '辅导课'+(' '+tutorial.group(1) if tutorial.group(1) else '')
    if region.kind=='bibliography':
        quoted=re.search(r'["“](.+?)["”]',text)
        if quoted:
            return text[:quoted.start(1)]+translator.translate(quoted.group(1))+text[quoted.end(1):]
        link=re.search(r'https?\s*:\s*/\s*/.*',text)
        if link:
            title=text[:link.start()].rstrip(': ')
            translated=title if re.fullmatch(r'[A-Z]{2,6}',title) else translator.translate(title)
            return translated+': '+re.sub(r'\s+','',link.group())
        # Names separated by commas stay intact; translate from the first
        # multiword title after the author list, including continuation lines.
        for match in re.finditer(r',\s*',text):
            next_field=text[match.end():].split(',')[0]
            if len(re.findall(r'[A-Za-z]{2,}',next_field))>=2 and not next_field.startswith('&'):
                return text[:match.end()]+translator.translate(text[match.end():].rstrip('.'))+'.'
    if region.machine_learning and translator.lang_out in {'zh','zh-CN','zh-Hans'}:
        table_headers={'name':'名称','cluster shape':'簇形状','principle':'原理','advantages':'优点','disadvantages':'缺点'}
        if region.kind in {'table','native_table'} and text.lower() in table_headers:
            return table_headers[text.lower()]
        terms={'kernel function':'核函数','kernel functions':'核函数',
               'kernel':'核函数','kernels':'核函数','epoch':'训练轮次','epochs':'训练轮次',
               'accuracy':'准确率','graphical model':'概率图模型','graphical models':'概率图模型',
               'margin distance':'间隔距离','margin':'间隔','naive bayes':'朴素贝叶斯',
               'likelihood':'似然','prior':'先验','posterior':'后验','variance':'方差','bias':'偏差',
               'poisson':'泊松','bernoulli':'伯努利','gaussian':'高斯','laplace':'拉普拉斯',
               'discriminative':'判别式','generative':'生成式',
               'classifier':'分类器','classifiers':'分类器','sharing':'共享',
               'log-likelihood':'对数似然','regularization':'正则化',
               'laplace smoothing':'拉普拉斯平滑','laplacian smoothing':'拉普拉斯平滑',
               'hinge loss':'合页损失','cross-entropy loss':'交叉熵损失',
               'decision boundary':'决策边界','decision boundaries':'决策边界',
               'convex':'凸函数','support vector':'支持向量','support vectors':'支持向量',
               'weight':'权重','weights':'权重','assignment':'分配',
               'class':'类别','classes':'类别','mean':'均值','means':'均值',
               'cluster':'簇','clusters':'簇','consensus set':'一致集',
               'principal component':'主成分','principal components':'主成分',
               'principal':'主','component':'成分','components':'成分','assignments':'分配',
               'feature':'特征','features':'特征','channel':'通道','channels':'通道',
               'train':'训练','training':'训练','encoder':'编码器','decoder':'解码器',
               'autoencoder':'自编码器','autoencoders':'自编码器','auto-encoder':'自编码器','auto-encoders':'自编码器',
               'transformer':'Transformer','transformers':'Transformer','glove':'GloVe',
               'eigenvalue':'特征值','eigenvalues':'特征值','eigenvector':'特征向量','eigenvectors':'特征向量',
               'bandwidth':'带宽','low-pass':'低通','high-pass':'高通','lowpass':'低通',
               'scaleshift':'缩放与平移','lowerdimensional':'低维',
               'token':'词元','tokens':'词元'}
        if region.geometric:
            terms.update({'translation':'平移','translations':'平移'})
        terminology='|'.join(re.escape(term) for term in sorted(terms,key=len,reverse=True))
        abbreviations='|'.join(re.escape(term) for term in sorted(ML_ABBREVIATIONS,key=len,reverse=True))
        pattern=r'(?<![A-Za-z])(?:(?i:'+terminology+r')|'+abbreviations+r')(?![A-Za-z])'
        values=[]
        def protect(match):
            token=match.group()
            values.append(token if token in ML_ABBREVIATIONS else terms[token.lower()])
            return '{v'+str(len(values)-1)+'}'
        encoded=re.sub(pattern,protect,text)
        translated=translator.translate(encoded)
        return re.sub(r'\{\s*v(\d+)\s*\}',lambda match:values[int(match.group(1))],translated)
    return translator.translate(text)


def render_regions(source_page,target_page,regions,font,rotation=0):
    """Cover only source text bounds with sampled background, inside its owner."""
    pix=source_page.get_pixmap(matrix=pymupdf.Matrix(2,2),colorspace=pymupdf.csRGB,alpha=False)
    pixels=np.frombuffer(pix.samples,np.uint8).reshape(pix.height,pix.width,3)
    with pymupdf.open() as overlay:
        page=overlay.new_page(width=source_page.rect.width,height=source_page.rect.height)
        archive=pymupdf.Archive((font.buffer,'structured.ttf'))
        for region in regions:
            for line in region.lines:
                rect=(line.rect+(-.7,-.7,.7,.7)) & region.rect
                x0,y0,x1,y1=(int(v*2) for v in rect)
                sample=pixels[max(0,y0):y1,max(0,x0):x1]
                flat=sample.reshape(-1,3)
                # Modal color preserves a slide's gray/blue background instead
                # of painting white rectangles across it.
                quantized=(flat//8)*8
                colors,counts=np.unique(quantized,axis=0,return_counts=True)
                background=colors[int(np.argmax(counts))].astype(float)/255
                near=np.linalg.norm(flat.astype(float)-background*255,axis=1)<18
                if near.any():
                    background=np.median(flat[near],axis=0)/255
                if region.kind in {'table','native_table'} and float(np.mean(background))<.78:
                    region.color=(1,1,1)
                page.draw_rect(rect,color=None,fill=tuple(background))
        # OCR boxes on adjacent rows can overlap slightly. Erase every source
        # run before writing any translation, so a later mask cannot erase text
        # just written into the preceding cell.
        for region in regions:
            color='white' if region.color==(1,1,1) else '#151515'
            css=("@font-face {font-family:translated;src:url(structured.ttf);}"
                 f"body {{font-family:translated;font-size:{region.size}pt;line-height:1.1;margin:0;color:{color};}}"
                 f"p {{margin:0;overflow-wrap:break-word;text-align:{region.align};}}")
            page.insert_htmlbox(region.rect,f'<p>{translated_html(region.translated)}</p>',css=css,archive=archive,
                                scale_low=0,rotate=region.rotation)
        tables=[region.rect for region in regions if region.kind=='table']
        if tables:
            bounds=(_union(tables)+(-3,-3,3,3)) & source_page.rect
            crop=source_page.get_pixmap(matrix=pymupdf.Matrix(2,2),clip=bounds,colorspace=pymupdf.csRGB,alpha=False)
            rgb=np.frombuffer(crop.samples,np.uint8).reshape(crop.height,crop.width,3)
            dark=(np.max(rgb,axis=2)<180).astype(np.uint8)*255
            horizontal=cv2.morphologyEx(dark,cv2.MORPH_OPEN,np.ones((1,max(40,crop.width//12)),np.uint8))
            vertical=cv2.morphologyEx(dark,cv2.MORPH_OPEN,np.ones((max(40,crop.height//20),1),np.uint8))
            alpha=cv2.bitwise_or(horizontal,vertical)
            bgra=np.dstack((rgb[:,:,::-1],alpha))
            png=cv2.imencode('.png',bgra)[1].tobytes()
            exact=pymupdf.Rect(crop.x/2,crop.y/2,(crop.x+crop.width)/2,(crop.y+crop.height)/2)
            page.insert_image(exact,stream=png)
        target_page.show_pdf_page(target_page.rect,overlay,0,rotate=rotation)


def _translate_and_render(source_page,target_page,translator,font,regions,thread,rotation,cancellation_event):
    def translate(region):
        if cancellation_event and cancellation_event.is_set():
            import asyncio
            raise asyncio.CancelledError('task cancelled')
        region.translated=translate_region(region,translator)
    with ThreadPoolExecutor(max_workers=max(1,thread)) as executor:
        list(executor.map(translate,regions))
    if regions:
        original_rotation=target_page.rotation
        target_page.set_rotation(0)
        try:
            render_regions(source_page,target_page,regions,font,rotation)
        finally:
            target_page.set_rotation(original_rotation)
    return regions


def translate_structured_page(source_page,target_page,translator,font,detection,image_rects,thread=4,rotation=0,cancellation_event=None,ocr_words=()):
    lines,rgb,scale=read_lines(source_page)
    # A small high-resolution table becomes illegible when the whole page is
    # reduced to the detector's maximum size. Recognize its own crop at the
    # useful resolution, then put the lines back into page coordinates.
    for box in detection.boxes:
        if detection.names[int(box.cls)]!='table':
            continue
        bounds=pymupdf.Rect(*box.xyxy)
        if not any((bounds & rect).get_area()>bounds.get_area()*.5 for rect in image_rects):
            continue
        fine_scale=min(6.0,1600/max(bounds.width,bounds.height))
        if fine_scale>2.2:
            fine,_,_=read_lines(source_page,(bounds+(-2,-2,2,2)) & source_page.rect,fine_scale)
            lines=[line for line in lines if _center(line.rect) not in bounds]+fine
    regions,handled=build_regions(source_page,detection,image_rects,lines,rgb,scale)
    _translate_and_render(source_page,target_page,translator,font,regions,thread,rotation,cancellation_event)
    return regions,handled


def translate_native_tables(source_page,target_page,translator,font,thread=4,cancellation_event=None):
    """Translate native ruled-table cells which the classic engine excludes.

    Use native cell text rather than OCR. Equations and artwork remain with the
    original renderer, and each translated cell keeps its own geometric bounds.
    """
    rotation=source_page.rotation
    source_page.set_rotation(0)
    try:
        regions=[]
        context=source_page.get_text()
        machine_learning=bool(re.search(r'machine learning|supervised|neural|training|Bayes|regression|autoencoder',context,re.I))
        images=[pymupdf.Rect(info['bbox']) for info in source_page.get_image_info()]
        for table in source_page.find_tables().tables:
            if table.row_count<2 or table.col_count<2:
                continue
            for coordinates in table.cells:
                if coordinates is None:
                    continue
                cell=pymupdf.Rect(coordinates)
                if any((cell & image).get_area()>cell.get_area()*.1 for image in images):
                    continue
                content=[]
                mathematical=False
                for block in source_page.get_text('dict',clip=cell,flags=pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES)['blocks']:
                    for line in block.get('lines',[]):
                        spans=line['spans']
                        text=''.join(span['text'] for span in spans).strip()
                        if text:
                            content.append(TextLine(pymupdf.Rect(line['bbox']),text))
                            mathematical=mathematical or any(re.search(r'CMMI|CMSY|CMEX|Math|Symbol',span['font'],re.I) for span in spans)
                text=_join_lines(content)
                if not _has_words(text) or _reference_code(text) or mathematical or re.search(r'[=∑∏∫≤≥_^]',text):
                    continue
                size=_font_size(content)
                inset=cell+(0.7,0.5,-0.7,-0.5)
                # Clear the cell interior, including any upstream partial
                # translation; the source grid lies outside this inset.
                centered=abs(_center(_union([line.rect for line in content])).x-_center(cell).x)<cell.width*.08
                region=TextRegion(inset,[TextLine(inset,text)],'native_table',size,
                    align='center' if centered else 'left',text=text,machine_learning=machine_learning)
                regions.append(region)
        return _translate_and_render(source_page,target_page,translator,font,regions,thread,0,cancellation_event)
    finally:
        source_page.set_rotation(rotation)


def translate_native_charts(source_page,target_page,translator,font,model,thread=4,cancellation_event=None):
    """Vector plots have usable text already; leave their curves and data native."""
    rotation=source_page.rotation
    source_page.set_rotation(0)
    try:
        return _translate_native_charts_upright(source_page,target_page,translator,font,model,thread,cancellation_event)
    finally:
        source_page.set_rotation(rotation)


def _translate_native_charts_upright(source_page,target_page,translator,font,model,thread,cancellation_event):
    # Native text bounds are unrotated. Detection, sampling and the overlay must
    # use that same frame, including PDF pages carrying a /Rotate entry.
    lines=[]
    for block in source_page.get_text('dict',flags=pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES)['blocks']:
        for line in block.get('lines',[]):
            text=''.join(span['text'] for span in line['spans']).strip()
            if text:
                direction=line.get('dir',(1,0))
                lines.append(TextLine(pymupdf.Rect(line['bbox']),text,rotation=90 if direction[1]<-.5 else 270 if direction[1]>.5 else 0))
    if not is_chart(lines,source_page.rect,None,1):
        return []
    pix=source_page.get_pixmap(colorspace=pymupdf.csRGB,alpha=False)
    rgb=np.frombuffer(pix.samples,np.uint8).reshape(pix.height,pix.width,3)
    detection=model.predict(rgb[:,:,::-1],imgsz=max(32,int(pix.height/32)*32))[0]
    regions=[]
    for box in detection.boxes:
        if detection.names[int(box.cls)]!='figure':
            continue
        bounds=pymupdf.Rect(*box.xyxy)
        content=[line for line in lines if _center(line.rect) in bounds]
        if not is_chart(content,bounds,rgb,1):
            continue
        margin=float(np.median([min(line.rect.width,line.rect.height) for line in content]))*2.5
        label_bounds=(bounds+(-margin,-margin,margin,margin)) & source_page.rect
        content=[line for line in lines if _center(line.rect) in label_bounds]
        for line in content:
            if _has_words(line.text):
                regions.append(TextRegion((line.rect+(-.3,-.3,.3,.3))&label_bounds,[line],'chart',_font_size([line]),text=line.text,rotation=line.rotation))
    machine_learning=any(re.search(r'training|validation|neural|regression',line.text,re.I) for line in lines)
    for region in regions:
        region.machine_learning=machine_learning
    return _translate_and_render(source_page,target_page,translator,font,regions,thread,0,cancellation_event)

"""Translate prose in dense native mathematics without reflowing any notation.

The classic engine remains the normal route. Formula-heavy native sheets use
their original page as the geometric base; prose runs occupy only their source
line's available space. Fractions, scripts, operators and drawing paths never
pass through a translator or a new line-breaking algorithm.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import asyncio
import re

import numpy as np
import pymupdf

MATH_FONT = re.compile(r'CM(?:R|MI|SY|EX|SS|TI|BX)\d|MSAM|MSBM|Math|Symbol', re.I)
OPERATORS = frozenset('min max arg argmin argmax log ln exp sin cos tan tanh lim softmax sigmoid relu sign diag det tr trace rank norm median var cov'.split())
ACRONYMS = frozenset('MLE MAP MLP LS LR OLS LASSO SVM KSVM PCA LSA FA CCA KDE GMM EM RF SGD CNN CNNs FCNN AE MAE VAE GAN RNN LLM OMP RANSAC DFT IDFT TF IDF NB BoW RBF LDR SVD NMF NTK GPR SVR RFR FLD LDA MSE RMSE PPL CDF PDF GPT RLHF YOLO MCMC ELBO KL ReLU BLEU ROUGE'.split())
SHORT_WORDS = frozenset('a an as at be by do go he if in is it me my no of on or so to up us we'.split())
GLUE = {'if':'若','for':'对','to':'至','where':'其中','and':'且','or':'或','when':'当','with':'含'}
PROSE_WORD = re.compile(r'^[①-⑳➀-➉\(\[“\"\']*[A-Za-z]+(?:[-’\'][A-Za-z]+)*[\)\],:;.!?“”\"\']*$')


@dataclass
class ProseRun:
    rect: pymupdf.Rect
    masks: list
    baseline: float
    size: float
    color: tuple
    text: str
    translated: str = ''


def _characters(page):
    lines={}
    for block in page.get_text('rawdict',flags=pymupdf.TEXTFLAGS_RAWDICT & ~pymupdf.TEXT_PRESERVE_IMAGES)['blocks']:
        for number,line in enumerate(block.get('lines',[])):
            if abs(line.get('dir',(1,0))[1])>.1:
                continue
            entries=[]
            for span in line['spans']:
                for char in span['chars']:
                    entries.append((char,span['font'],span['size'],span['color']))
            lines[(block['number'],number)]=entries
    return lines


def _word_characters(word,lines):
    rect=pymupdf.Rect(word[:4])
    return [entry for entry in lines.get((word[5],word[6]),[])
            if pymupdf.Point((entry[0]['bbox'][0]+entry[0]['bbox'][2])/2,
                             (entry[0]['bbox'][1]+entry[0]['bbox'][3])/2) in rect]


def _is_prose(word,entries):
    text=word[4]
    core=text.strip('()[],:;.!?“”\"\'①②③④⑤⑥⑦⑧⑨➀➁➂➃➄➅➆➇➈')
    letters=[entry for entry in entries if entry[0]['c'].isalpha()]
    if not PROSE_WORD.fullmatch(text) or core.lower() in OPERATORS or core in ACRONYMS:
        return False
    if len(core)>=2 and core.isupper() and letters and all(re.search(r'Ital|Oblique',entry[1],re.I) for entry in letters):
        return False
    if letters and any(MATH_FONT.search(font) or re.search(r'Mono|Courier|Code|CMTT|NimbusMon',font,re.I) for _,font,_,_ in letters):
        return False
    if letters and min(entry[2] for entry in letters)<max(entry[2] for entry in letters)*.85:
        return False
    if len(core)<=2 and core.lower() not in SHORT_WORDS:
        return False
    if len(core)==1 and (core!='a' or any('Ital' in font for _,font,_,_ in entries)):
        return False
    if re.search(r'[a-z][A-Z][a-z]',core):
        return False
    return True


def _split_words(words,lines):
    """Separate prose from arrows/notation fused into one PDF word token."""
    output=[]
    for word in words:
        if PROSE_WORD.fullmatch(word[4]) or not re.search('[A-Za-z]',word[4]):
            output.append(word)
            continue
        entries=_word_characters(word,lines)
        segments=[]
        for entry in entries:
            char=entry[0]['c']
            letters=char.isascii() and (char.isalpha() or char in "-'’")
            if not segments or segments[-1][0]!=letters:
                segments.append((letters,[]))
            segments[-1][1].append(entry)
        for _,content in segments:
            box=pymupdf.Rect(content[0][0]['bbox'])
            for entry in content[1:]:
                box|=pymupdf.Rect(entry[0]['bbox'])
            text=''.join(entry[0]['c'] for entry in content)
            output.append((*box,text,*word[5:8]))
    return output


def _fraction_annotations(page,runs,lines):
    """Keep tight numerator/denominator labels with their source fraction.

    PDF word boxes can fuse a denominator and the full-size punctuation after
    the fraction. That box crosses the bar, and CJK ascenders need more room
    than the original Latin x-height. Treat those annotations as notation.
    """
    tables=[pymupdf.Rect(t.bbox) for t in page.find_tables().tables
            if t.row_count>=2 and t.col_count>=2]
    chars=[entry[0] for entries in lines.values() for entry in entries if entry[0]['c'].isalnum()]
    body_size=float(np.median([entry[2] for entries in lines.values() for entry in entries
                              if entry[0]['c'].isalpha() and not MATH_FONT.search(entry[1])]))
    rules=[]
    for path in page.get_drawings():
        if path['type']!='s':
            continue
        for item in path['items']:
            if item[0]!='l':
                continue
            a,b=item[1:3]
            left,right=sorted((a.x,b.x))
            y=(a.y+b.y)/2
            if abs(a.y-b.y)>.3 or not 4<right-left<page.rect.width*.25 or any(pymupdf.Point((left+right)/2,y) in t for t in tables):
                continue
            nearby=[c for c in chars if left<(c['bbox'][0]+c['bbox'][2])/2<right and abs(c['origin'][1]-y)<14]
            if any(c['origin'][1]<y for c in nearby) and any(c['bbox'][1]>y-.4 for c in nearby):
                rules.append((left,right,y))
    return [run for run in runs if not (run.size<body_size*.9 and any(
        min(run.rect.x1,right)-max(run.rect.x0,left)>run.rect.width*.65 and
        abs(run.baseline-y)<run.size*1.6 for left,right,y in rules))]


def prose_runs(page):
    words=page.get_text('words')
    lines=_characters(page)
    chars=[entry for entries in lines.values() for entry in entries if entry[0]['c'].strip()]
    math_count=sum(bool(MATH_FONT.search(entry[1])) for entry in chars)
    if len(words)<150 or math_count<80 or math_count/max(1,len(chars))<.045:
        return []
    words=_split_words(words,lines)
    grouped={}
    for word in words:
        grouped.setdefault((word[5],word[6]),[]).append(word)
    extra_masks={}
    keys=sorted(grouped)
    for row in grouped.values():
        row.sort(key=lambda word:word[0])
    for key in keys:
        row=grouped[key]
        if not row or not re.fullmatch('[A-Za-z]{2,}-',row[-1][4]):
            continue
        last=row[-1]
        height=last[3]-last[1]
        left=min(items[0][0] for row_key,items in grouped.items() if row_key[0]==key[0] and items)
        following=[items for row_key,items in grouped.items() if items and
            height*.65<items[0][1]-last[1]<height*2.3 and
            abs(items[0][0]-(left if row_key[0]==key[0] else row[0][0]))<height*2.2 and
            .85*height<items[0][3]-items[0][1]<1.2*height and
            re.fullmatch(r'''[a-z]{2,}(?:[’'][a-z]+)?[),.:;!?"'”’]*''',items[0][4])]
        if not following:
            continue
        next_row=min(following,key=lambda items:items[0][1]-last[1])
        first=next_row[0]
        # Keep the joined word in the longer fragment's original slot. Short
        # prefixes such as en-/coder otherwise squeeze a full Chinese word
        # into only two letters' space at the preceding line end.
        owner=first if len(re.sub('[^A-Za-z]','',first[4]))>=len(last[4])-1 else last
        other=last if owner is first else first
        joined=list(owner)
        joined[4]=last[4][:-1]+first[4]
        extra_masks[tuple(joined[:4])]=pymupdf.Rect(other[:4])
        if owner is first:
            next_row[0]=tuple(joined)
            row.pop()
        else:
            row[-1]=tuple(joined)
            del next_row[0]
    runs=[]
    from desktop_runtime import _DATE_PATTERN
    for key,row in grouped.items():
        row.sort(key=lambda word:word[0])
        full=' '.join(word[4] for word in row)
        groups=[]
        if _DATE_PATTERN.fullmatch(full):
            groups=[row]
        else:
            active=[]
            for word in row:
                entries=_word_characters(word,lines)
                if _is_prose(word,entries):
                    if active and word[0]-active[-1][2]>max(3,(word[3]-word[1])*1.5):
                        groups.append(active)
                        active=[]
                    active.append(word)
                elif active:
                    groups.append(active)
                    active=[]
            if active:
                groups.append(active)
        for group in groups:
            entries=[entry for word in group for entry in _word_characters(word,lines)]
            if not entries:
                continue
            box=pymupdf.Rect(group[0][:4])
            for word in group[1:]:
                box|=pymupdf.Rect(word[:4])
            size=float(np.median([entry[2] for entry in entries]))
            following=[word[0] for word in row if word[0]>=box.x1-.1 and word not in group]
            right=min(following,default=box.x1+size*.35)-.3
            box.x1=max(box.x1,right)
            color=entries[0][3]
            color=((color>>16 & 255)/255,(color>>8 & 255)/255,(color & 255)/255)
            masks=[pymupdf.Rect(entry[0]['bbox']) for entry in entries if entry[0]['c'].strip()]
            masks.extend(extra_masks[tuple(word[:4])] for word in group if tuple(word[:4]) in extra_masks)
            runs.append(ProseRun(box,masks,
                float(np.median([entry[0]['origin'][1] for entry in entries])),size,color,
                ' '.join(word[4] for word in group)))
    return _fraction_annotations(page,runs,lines)


def translate_dense_native_page(source_page,target_page,translator,font,thread=4,cancellation_event=None):
    rotation=source_page.rotation
    source_page.set_rotation(0)
    try:
        runs=prose_runs(source_page)
        if not runs:
            return False
        from desktop_runtime import _DATE_PATTERN, protected_value
        from structured_images import TextRegion, translate_region
        context=source_page.get_text()
        ml=bool(re.search(r'machine learning|regression|clustering|neural|Bayes|auto.encoder|perceptron',context,re.I))
        def translate(run):
            if cancellation_event and cancellation_event.is_set():
                raise asyncio.CancelledError('task cancelled')
            if translator.lang_out.lower().startswith('zh') and _DATE_PATTERN.fullmatch(run.text):
                run.translated=protected_value(run.text,translator.lang_out)
            elif translator.lang_out.lower().startswith('zh') and run.text.lower() in GLUE:
                run.translated=GLUE[run.text.lower()]
            else:
                region=TextRegion(run.rect,[],'plain text',run.size,text=run.text,machine_learning=ml)
                region.geometric=bool(re.search(r'data augmentation|translation invariance',context,re.I))
                run.translated=translate_region(region,translator)
        with ThreadPoolExecutor(max_workers=max(1,thread)) as executor:
            list(executor.map(translate,runs))
        if cancellation_event and cancellation_event.is_set():
            raise asyncio.CancelledError('task cancelled')
        pix=source_page.get_pixmap(colorspace=pymupdf.csRGB,alpha=False)
        pixels=np.frombuffer(pix.samples,np.uint8).reshape(pix.height,pix.width,3)
        original_rotation=target_page.rotation
        target_page.set_rotation(0)
        try:
            stream=target_page.parent.get_new_xref()
            target_page.parent.update_object(stream,'<<>>')
            target_page.parent.update_stream(stream,b'')
            target_page.set_contents(stream)
            target_page.show_pdf_page(target_page.rect,source_page.parent,source_page.number)
            with pymupdf.open() as overlay:
                page=overlay.new_page(width=source_page.rect.width,height=source_page.rect.height)
                page.insert_font(fontname='native_cn',fontbuffer=font.buffer)
                erasures=page.new_shape()
                for run in runs:
                    for mask in run.masks:
                        x0,y0,x1,y1=(int(v) for v in mask)
                        sample=pixels[max(0,y0):y1+1,max(0,x0):x1+1].reshape(-1,3)
                        colors,counts=np.unique((sample//8)*8,axis=0,return_counts=True)
                        background=colors[int(np.argmax(counts))].astype(float)
                        near=np.linalg.norm(sample.astype(float)-background,axis=1)<18
                        background=np.median(sample[near],axis=0)/255
                        erasures.draw_rect(mask+(-.05,-.05,.05,.05))
                        erasures.finish(color=None,fill=tuple(background))
                erasures.commit()
                for run in runs:
                    text=re.sub(r'\s+',' ',run.translated).strip()
                    width=font.text_length(text,fontsize=1)
                    size=min(run.size,run.rect.width/max(.01,width))
                    page.insert_text((run.rect.x0,run.baseline),text,fontname='native_cn',fontsize=size,color=run.color)
                target_page.show_pdf_page(target_page.rect,overlay,0)
        finally:
            target_page.set_rotation(original_rotation)
        return True
    finally:
        source_page.set_rotation(rotation)

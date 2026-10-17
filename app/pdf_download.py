"""Download PDF links with filenames usable on Windows."""

from email.message import Message
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

import gradio as gr
import requests


def pdf_filename(url, content_disposition=None):
    header = Message()
    if content_disposition:
        header['Content-Disposition'] = content_disposition
    filename = header.get_filename() or unquote(urlsplit(url).path).rsplit('/', 1)[-1]
    filename = re.split(r'[/\\]', filename)[-1]
    filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', filename).strip(' .')
    stem = Path(filename).stem.rstrip(' .') or 'document'
    if re.match(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', stem, re.I):
        stem = '_' + stem
    return stem + '.pdf'


def download_with_limit(url, save_path, size_limit):
    total_size = 0
    with requests.get(url, stream=True, timeout=10) as response:
        response.raise_for_status()
        destination = Path(save_path) / pdf_filename(url, response.headers.get('Content-Disposition'))
        with destination.open('wb') as file:
            for chunk in response.iter_content(chunk_size=1024):
                total_size += len(chunk)
                if size_limit and total_size > size_limit:
                    raise gr.Error('Exceeds file size limit')
                file.write(chunk)
    return destination

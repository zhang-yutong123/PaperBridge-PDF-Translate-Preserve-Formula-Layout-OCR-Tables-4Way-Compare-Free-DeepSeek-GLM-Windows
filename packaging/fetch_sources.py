"""Fetch redistributable upstream source and asset licenses from official sites."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import shutil
import time
from urllib.request import Request, urlopen, urlretrieve

ROOT = Path(__file__).resolve().parent
sources = ROOT / "source-archives"
licenses = ROOT.parent / "licenses"
sources.mkdir(exist_ok=True)
licenses.mkdir(exist_ok=True)

source_url = "https://files.pythonhosted.org/packages/40/fc/dd8776dc5c2f8cf0e51cf81a5f1de3840996bed7ca03ec768b0733024fb9/pymupdf-1.25.2.tar.gz"
target = sources / "pymupdf-1.25.2.tar.gz"
with urlopen(Request(source_url, method="HEAD")) as response:
    length = int(response.headers["Content-Length"])
if not target.exists() or target.stat().st_size != length:
    chunk = 1024 * 1024
    count = (length + chunk - 1) // chunk
    parts_directory = sources / "download-parts"
    parts_directory.mkdir(exist_ok=True)
    # The earlier streaming transfer contains a valid prefix; retain those
    # bytes instead of downloading the same source bytes again.
    if target.exists():
        with target.open("rb") as prefix:
            for index in range(count):
                data = prefix.read(chunk)
                if not data:
                    break
                part = parts_directory / f"pymupdf-{index:03}.part"
                if not part.exists() or part.stat().st_size < len(data):
                    part.write_bytes(data)
    def download_part(index):
        start, end = index * chunk, min(length - 1, (index + 1) * chunk - 1)
        part = parts_directory / f"pymupdf-{index:03}.part"
        for attempt in range(8):
            size = part.stat().st_size if part.exists() else 0
            if size == end - start + 1:
                print(f"Source part {index + 1}/{count} complete", flush=True)
                return part
            try:
                request = Request(source_url, headers={"Range": f"bytes={start + size}-{end}"})
                with urlopen(request, timeout=60) as response, part.open("ab") as output:
                    if response.status != 206:
                        raise RuntimeError("Source host did not accept a byte range")
                    while block := response.read(64 * 1024):
                        output.write(block)
            except OSError:
                time.sleep(min(attempt + 1, 4))
        raise RuntimeError(f"Source part {index + 1} incomplete; rerun to continue")
    with ThreadPoolExecutor(max_workers=16) as pool:
        parts = list(pool.map(download_part, range(count)))
    assembled = sources / "pymupdf-1.25.2.complete"
    with assembled.open("wb") as output:
        for part in parts:
            with part.open("rb") as source:
                shutil.copyfileobj(source, output)
    assembled.replace(target)

def download_mupdf():
    url = "https://mupdf.com/downloads/archive/mupdf-1.25.2-source.tar.gz"
    destination = sources / "mupdf-1.25.2-source.tar.gz"
    with urlopen(Request(url, method="HEAD")) as response:
        length = int(response.headers["Content-Length"])
        resolved_url = response.url
    if destination.exists() and destination.stat().st_size == length:
        return
    chunk = 1024 * 1024
    count = (length + chunk - 1) // chunk
    parts_directory = sources / "download-parts"
    parts_directory.mkdir(exist_ok=True)
    def download_part(index):
        start, end = index * chunk, min(length - 1, (index + 1) * chunk - 1)
        part = parts_directory / f"mupdf-{index:03}.part"
        for attempt in range(8):
            size = part.stat().st_size if part.exists() else 0
            if size == end - start + 1:
                print(f"MuPDF source part {index + 1}/{count} complete", flush=True)
                return part
            try:
                request = Request(resolved_url, headers={"Range": f"bytes={start + size}-{end}"})
                with urlopen(request, timeout=60) as response, part.open("ab") as output:
                    if response.status != 206:
                        raise RuntimeError("Source host did not accept a byte range")
                    while block := response.read(64 * 1024):
                        output.write(block)
            except OSError:
                time.sleep(min(attempt + 1, 4))
        raise RuntimeError(f"MuPDF source part {index + 1} incomplete; rerun to continue")
    with ThreadPoolExecutor(max_workers=16) as pool:
        parts = list(pool.map(download_part, range(count)))
    assembled = sources / "mupdf-1.25.2.complete"
    with assembled.open("wb") as output:
        for part in parts:
            with part.open("rb") as source:
                shutil.copyfileobj(source, output)
    assembled.replace(destination)

download_mupdf()
for name, url in {
    "SourceHanSerif-OFL.txt": "https://raw.githubusercontent.com/adobe-fonts/source-han-serif/release/LICENSE.txt",
    "Tesseract-Apache-2.0.txt": "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/LICENSE",
    "RapidOCR-Apache-2.0.txt": "https://raw.githubusercontent.com/RapidAI/RapidOCR/main/LICENSE",
    "Python-3.12-LICENSE.txt": "https://raw.githubusercontent.com/python/cpython/v3.12.14/LICENSE",
    "PyInstaller-license-and-bootloader-exception.txt": "https://raw.githubusercontent.com/pyinstaller/pyinstaller/v6.22.3/COPYING.txt",
    "DocLayout-YOLO-LICENSE.txt": "https://raw.githubusercontent.com/opendatalab/DocLayout-YOLO/main/LICENSE",
}.items():
    urlretrieve(url, licenses / name)
print("Upstream source archives and asset licenses downloaded.")

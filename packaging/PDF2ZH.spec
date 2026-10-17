# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import os
from PyInstaller.utils.hooks import collect_all, collect_data_files, copy_metadata

ROOT = Path(SPECPATH).parent
APP = ROOT / "app"
PACKAGES = Path(os.environ["PDF2ZH_BUILD_PACKAGES"])
datas = [(str(APP / "assets"), "assets")]
datas += collect_data_files("pdf2zh", include_py_files=True)
datas += collect_data_files("safehttpx")
datas += collect_data_files("groovy")
binaries = []
hiddenimports = ["app", "pdf2zh.gui", "uvicorn.logging", "uvicorn.loops.auto",
                 "uvicorn.protocols.http.auto", "uvicorn.protocols.websockets.auto",
                 "uvicorn.lifespan.on", "babeldoc.docvision.doclayout"]
for package in ["gradio", "gradio_client", "gradio_pdf", "rapidocr_onnxruntime", "onnxruntime"]:
    package_data, package_binaries, package_imports = collect_all(package)
    datas += package_data
    binaries += package_binaries
    hiddenimports += package_imports
for distribution in ["pdf2zh", "gradio", "gradio_client", "pymupdf", "babeldoc", "rapidocr_onnxruntime"]:
    datas += copy_metadata(distribution)

a = Analysis([str(ROOT / "packaging" / "desktop_entry.py")],
    pathex=[str(APP), str(PACKAGES)], binaries=binaries, datas=datas,
    hiddenimports=hiddenimports, hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=["torch", "tensorflow", "matplotlib", "IPython", "pytest", "notebook"],
    noarchive=False, optimize=0)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="PDF论文翻译",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=False, disable_windowed_traceback=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="PDF论文翻译")

"""Desktop reader using the installed PDFMathTranslate translation backend."""
import importlib.util
import os
from pathlib import Path
import sys
import types
import tempfile
import shutil
import pymupdf

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("PDF2ZH_DATA_DIR") or (
    Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "PDF2ZH"
    if getattr(sys, "frozen", False) else APP_DIR
))
DATA_DIR.mkdir(parents=True, exist_ok=True)
TEMP_DIR = DATA_DIR / 'cache' / 'tmp'
TEMP_DIR.mkdir(parents=True,exist_ok=True)
tempfile.tempdir = str(TEMP_DIR)
MODEL_PATH = APP_DIR / "assets" / "doclayout_yolo_docstructbench_imgsz1024.onnx"
FONT_PATH = APP_DIR / "assets" / "SourceHanSerifCN-Regular.ttf"
os.chdir(DATA_DIR)
os.environ["GRADIO_ANALYTICS_ENABLED"] = "False"

# The upstream engine initializes a home-directory cache during import; this UI
# replaces it with its own database immediately afterwards.
_expanduser = os.path.expanduser
def _desktop_expanduser(path):
    if isinstance(path, str) and path == "~":
        return str(DATA_DIR / "cache" / "home")
    if isinstance(path, str) and path.startswith("~" + os.sep):
        return str(DATA_DIR / "cache" / "home") + path[1:]
    return _expanduser(path)

os.path.expanduser = _desktop_expanduser
try:
    from pdf2zh.config import ConfigManager
finally:
    os.path.expanduser = _expanduser

from pdf2zh.doclayout import ModelInstance, OnnxModel
from desktop_runtime import install_runtime_fixes
from desktop_ui import build_ui
from pdf_input import prepare_pdf_file
from pdf_download import download_with_limit
from page_selection import select_pages

SETTINGS_PATH = DATA_DIR / 'settings.json'
if not SETTINGS_PATH.exists():
    shutil.copy2(APP_DIR / 'assets' / 'default-settings.json', SETTINGS_PATH)
configuration = ConfigManager.__new__(ConfigManager)
configuration._initialized = True
configuration._config_path = SETTINGS_PATH
configuration._load_config()
ConfigManager._instance = configuration
ConfigManager.set("NOTO_FONT_PATH", str(FONT_PATH))
from pdf2zh import cache
cache.db.close()
cache.db.init(str(DATA_DIR / 'cache' / 'translation.db'),pragmas={'journal_mode':'wal','busy_timeout':1000})
cache.db.create_tables([cache._TranslationCache],safe=True)
install_runtime_fixes()
if sys.platform == "win32":
    import image_text
    # MuPDF's Windows Tesseract binding cannot open a UTF-8 model path. Keep the
    # public language model at an ASCII Windows location, including when the
    # install folder or the recipient's Windows user name contains Chinese.
    if not str(image_text.TESSDATA).isascii():
        shared_tessdata = Path(os.environ["PUBLIC"]) / "Documents" / "PDF2ZH-models" / "tessdata"
        shared_tessdata.mkdir(parents=True, exist_ok=True)
        language_model = shared_tessdata / "eng.traineddata"
        if not language_model.exists():
            shutil.copy2(image_text.TESSDATA / "eng.traineddata", language_model)
        image_text.TESSDATA = shared_tessdata
ModelInstance.value = OnnxModel(str(MODEL_PATH))

# Keep upstream translation and cancellation functions.
# The desktop reader is defined separately from that engine code.
gui_path = Path(importlib.util.find_spec("pdf2zh.gui").origin)
source = gui_path.read_text(encoding="utf-8").split("# The following code creates the GUI", 1)[0]
source = source.replace("BABELDOC_MODEL = OnnxModel.load_available()", "BABELDOC_MODEL = None")
source = source.replace("from babeldoc.docvision.doclayout import OnnxModel", "from pdf2zh.doclayout import OnnxModel")
source = source.replace("import asyncio\n", "import asyncio\nfrom datetime import datetime\n", 1)
source = source.replace('    cancellation_event_map[session_id] = asyncio.Event()',
    '    cancellation_event_map[session_id] = asyncio.Event()\n    if state.get("cancelled"):\n        raise gr.Error("Translation cancelled")')
source = source.replace('output = Path("pdf2zh_files")', 'output = Path("pdf2zh_files") / datetime.now().strftime("%Y%m%d-%H%M%S-%f")')
source = source.replace('    filename = os.path.splitext(os.path.basename(file_path))[0]',
    '    file_path = DESKTOP_PREPARE_PDF(file_path)\n    filename = os.path.splitext(os.path.basename(file_path))[0]')
source = source.replace('    file_raw = output / f"{filename}.pdf"', '    file_raw = Path(file_path)')
page_start = source.index('    if page_range != "Others":')
page_end = source.index('    lang_from = lang_map[lang_from]', page_start)
source = source[:page_start] + '''    with DESKTOP_PDF_OPEN(file_raw) as document:
        try:
            selected_page = DESKTOP_SELECT_PAGES(page_range, page_input, len(document))
        except ValueError as error:
            raise gr.Error(str(error)) from None
''' + source[page_end:]
for original, replacement in {
    '"Starting translation..."': '"正在开始翻译……"',
    '"Translating..."': '"正在翻译正文……"',
    '"Translation complete!"': '"翻译完成"',
    '"No input"': '"请先上传 PDF 或填写下载链接"',
    '"No output"': '"尚未生成翻译结果"',
    '"Translation cancelled"': '"已取消翻译"',
}.items():
    source = source.replace(original, replacement)

engine = types.ModuleType("pdf2zh.gui")
engine.__file__ = str(gui_path)
engine.__package__ = "pdf2zh"
engine.DESKTOP_PREPARE_PDF = prepare_pdf_file
engine.DESKTOP_PDF_OPEN = pymupdf.open
engine.DESKTOP_SELECT_PAGES = select_pages
sys.modules["pdf2zh.gui"] = engine
exec(compile(source, str(gui_path), "exec"), engine.__dict__)
engine.download_with_limit = download_with_limit

demo = build_ui(engine, (APP_DIR / "assets" / "prompt.txt").read_text(encoding="utf-8"))
if __name__ == "__main__":
    demo.launch(server_name="127.0.0.1", server_port=int(os.environ.get("PDF2ZH_PORT", "7868")), share=False,
                inbrowser="--open-browser" in sys.argv, show_error=True)

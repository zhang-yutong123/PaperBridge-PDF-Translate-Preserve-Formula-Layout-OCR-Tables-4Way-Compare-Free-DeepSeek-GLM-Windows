"""Windowed entry point for the self-contained Windows distribution."""
import ctypes
import json
import os
from pathlib import Path
import sys
import threading
import time
import traceback
from urllib.request import urlopen


PORT = int(os.environ.get("PDF2ZH_PORT", "7868"))
URL = f"http://127.0.0.1:{PORT}"
DATA = Path(os.environ.get("PDF2ZH_DATA_DIR") or Path(os.environ["LOCALAPPDATA"]) / "PDF2ZH")
DATA.mkdir(parents=True, exist_ok=True)
log = (DATA / "launcher.log").open("a", encoding="utf-8", buffering=1)
sys.stdout = sys.stderr = log


def is_running():
    try:
        with urlopen(URL + "/config", timeout=1) as response:
            return json.load(response).get("title") == "论文 PDF 翻译"
    except (OSError, ValueError):
        return False


def open_browser():
    if "--no-browser" not in sys.argv:
        os.startfile(URL)


def failure():
    traceback.print_exc()
    if "--no-browser" not in sys.argv:
        ctypes.windll.user32.MessageBoxW(None,
            "启动未完成。详细原因已记录在：\n" + str(DATA / "launcher.log"),
            "论文 PDF 翻译", 0x10)


def serve(ready, failed):
    try:
        import app
        app.demo.launch(server_name="127.0.0.1", server_port=PORT, share=False,
                        inbrowser=False, show_error=True, prevent_thread_lock=True)
        open_browser()
        ready.set()
        while True:
            time.sleep(3600)
    except Exception:
        failure()
        failed.set()


def main():
    # The mutex covers the interval before the HTTP socket is ready, so two
    # desktop double-clicks do not start two servers competing for one port.
    kernel = ctypes.windll.kernel32
    kernel.CreateMutexW.restype = ctypes.c_void_p
    mutex = kernel.CreateMutexW(None, False, f"Local\\PDF2ZHDesktop_{PORT}")
    existing = kernel.GetLastError() == 183
    if is_running():
        open_browser()
        return
    if existing:
        for _ in range(120):
            if is_running():
                open_browser()
                return
            time.sleep(0.5)
        ctypes.windll.user32.MessageBoxW(None,
            "程序正在启动，请稍后再试。若持续无法打开，请查看：\n" + str(DATA / "launcher.log"),
            "论文 PDF 翻译", 0x40)
        return

    ready, failed = threading.Event(), threading.Event()
    worker = threading.Thread(target=serve, args=(ready, failed), daemon=False)
    worker.start()
    if "--no-browser" not in sys.argv:
        import tkinter as tk
        splash = tk.Tk()
        splash.title("论文 PDF 翻译")
        splash.resizable(False, False)
        splash.geometry("400x125")
        tk.Label(splash, text="正在启动论文 PDF 翻译……", font=("Microsoft YaHei UI", 12)).pack(pady=(25, 10))
        tk.Label(splash, text="准备模型和阅读界面后，将自动打开浏览器。", font=("Microsoft YaHei UI", 9)).pack()

        def poll():
            if ready.is_set() or failed.is_set():
                splash.destroy()
            else:
                splash.after(200, poll)

        splash.after(200, poll)
        splash.mainloop()
    worker.join()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        failure()

"""Simple settings and four reading modes for the official translation engine."""
from pathlib import Path
import gradio as gr
from gradio_pdf import PDF
import pymupdf
from pdf2zh.config import ConfigManager
from pdf_input import prepare_pdf_file
from pdf_outputs import make_side_by_side
from page_selection import select_pages

SERVICE_LABELS = {
    "Bing": "必应翻译 · 免费，无需 API Key", "Google": "Google 翻译 · 免费，无需 API Key",
    "DeepSeek": "DeepSeek · 深度求索", "Zhipu": "智谱 GLM",
    "Ali Qwen-Translation": "通义千问 · 翻译模型", "OpenAI-liked": "自定义 OpenAI 兼容服务",
    "ModelScope": "魔搭 · ModelScope", "Silicon": "硅基流动 · SiliconFlow",
    "Ollama": "Ollama · 本地模型", "Argos Translate": "Argos · 本地翻译",
}
LANGUAGE_LABELS = {
    "English": "英语", "Simplified Chinese": "简体中文", "Traditional Chinese": "繁体中文",
    "French": "法语", "German": "德语", "Japanese": "日语", "Korean": "韩语",
    "Russian": "俄语", "Spanish": "西班牙语", "Italian": "意大利语",
}
READING_MODES = [("原文", "source"), ("译文", "mono"), ("左右对照", "side"), ("逐页对照", "dual")]
CSS = """
.gradio-container {max-width: 1680px !important; margin: auto;}
footer {display: none !important;}
#reading-settings {max-width: 390px;}
#reading-panel {min-width: 0;}
#reading-mode .wrap {gap: 8px;}
#reading-preview {min-height: 680px;}
#reading-preview canvas {max-width: 100%;}
#reading-status {min-height: 26px;}
@media (max-width: 850px) {
  #reading-settings {max-width: none;}
  #reading-preview {min-height: 480px;}
}
"""

def provider_fields(service_map, service):
    provider = service_map[service]
    fields = []
    for name, default in provider.envs.items():
        fields.append(gr.update(visible=True,
            label=("API Key" if "KEY" in name else "模型名称" if "MODEL" in name else name),
            type="password" if "KEY" in name or "SECRET" in name else "text",
            value=ConfigManager.get_env_by_translatername(provider, name, default)))
    for _ in range(max(len(p.envs) for p in service_map.values()) - len(fields)):
        fields.append(gr.update(visible=False, value=""))
    return fields

def save_settings(service_map, service, lang_from, lang_to, *values):
    provider = service_map[service]
    ConfigManager.set_translator_by_name(provider.name, dict(zip(provider.envs, values)))
    ConfigManager.set("PDF2ZH_DESKTOP_SERVICE", service)
    ConfigManager.set("PDF2ZH_LANG_FROM", lang_from)
    ConfigManager.set("PDF2ZH_LANG_TO", lang_to)
    return "配置已保存，下次打开自动使用。"

def preview_mode(mode, files):
    path = files.get(mode)
    return gr.update(value=path, starting_page=1), gr.update(value=path, visible=bool(path))

def source_preview(path):
    path = prepare_pdf_file(path)
    files = {"source": path} if path else {}
    return files, path, gr.update(choices=READING_MODES[:1], value="source"), gr.update(visible=False, value=None), ""

def build_ui(engine, default_prompt):
    service_map = engine.service_map
    saved_service = ConfigManager.get("PDF2ZH_DESKTOP_SERVICE", "Bing")

    def begin_translation(job_state):
        job_state.update(session_id=None, succeeded=False, cancelled=False, failure_message="")
        return (gr.update(interactive=False), gr.update(visible=True),
                gr.update(interactive=False), gr.update(interactive=False), "正在翻译，请稍候……")

    def run_translation(file_type, file_path, link, service, lang_from, lang_to,
                        page_range, pages, job_state, progress=gr.Progress(), *values):
        job_state.update(succeeded=False, failure_message="")
        try:
            if job_state.get("cancelled"):
                raise gr.Error("已取消翻译")
            if service in ("DeepSeek", "Zhipu") and not values[0].strip():
                raise gr.Error("请先填写所选服务的 API Key。")
            save_settings(service_map, service, lang_from, lang_to, *values)
            prompt = ConfigManager.get("PDF2ZH_DESKTOP_PROMPT") or default_prompt
            # Font, cache, formula handling and concurrency use built-in defaults.
            result = engine.translate_file(file_type, file_path, link, service, lang_from, lang_to,
                page_range, pages, prompt, 4, False, False, "", False, None,
                job_state, progress, *values)
            mono, dual = result[0], result[2]
            original = Path(mono).with_name(Path(mono).stem.removesuffix("-mono") + ".pdf")
            files = {"source": prepare_pdf_file(original), "mono": mono,
                     "dual": dual, "side": make_side_by_side(dual)}
            if job_state.get("cancelled"):
                raise gr.Error("已取消翻译")
            with pymupdf.open(mono) as document:
                count = len(document)
            selected_pages = select_pages(page_range, pages, count)
            completed = f"{count} 页" if selected_pages is None else f"文档 {count} 页，本次翻译 {len(selected_pages)} 页"
            job_state["succeeded"] = True
            return (files, gr.update(value=files["side"], starting_page=1),
                gr.update(choices=READING_MODES, value="side"),
                gr.update(value=files["side"], visible=True),
                f"已完成 · {completed}。左右对照为左侧原文、右侧译文；逐页对照交替显示原文与译文。")
        except Exception as error:
            # A cancelled or failed new task should not cover the previous
            # reading result with Gradio's error overlay.
            reason = str(getattr(error, "message", str(error)))
            message = "已取消，可以重新开始。" if job_state.get("cancelled") else f"本次未完成：{reason}"
            job_state["failure_message"] = message
            if not job_state.get("cancelled"):
                gr.Warning(reason)
            return gr.skip(), gr.skip(), gr.skip(), gr.skip(), message
        finally:
            engine.cancellation_event_map.pop(job_state.get("session_id"), None)
            job_state["session_id"] = None

    def cancel_translation(job_state):
        job_state["cancelled"] = True
        engine.stop_translate_file(job_state)
        return "正在取消，当前处理结束后停止。"

    def finish_controls(job_state):
        message = gr.skip() if job_state.get("succeeded") else job_state.get("failure_message", "本次未完成，请重试。")
        return (gr.update(interactive=True), gr.update(visible=False),
                gr.update(interactive=True), gr.update(interactive=True), message)

    def select_input(kind, path):
        return (gr.update(visible=kind == "File"), gr.update(visible=kind == "Link"),
                *source_preview(path if kind == "File" else None))

    def toggle_reading(focused):
        return not focused, gr.update(visible=focused), gr.update(value="显示设置" if not focused else "展开阅读区")

    with gr.Blocks(title="论文 PDF 翻译", theme=gr.themes.Default(primary_hue="blue"), css=CSS) as demo:
        gr.Markdown("# 论文 PDF 翻译\n上传后开始翻译，直接切换译文和两种对照；需要留存时再下载。 · 2026.10.17.1")
        job_state = gr.State({"session_id": None})
        files = gr.State({})
        reading_focus = gr.State(False)
        with gr.Row():
            with gr.Column(scale=1, min_width=300, elem_id="reading-settings") as settings_panel:
                gr.Markdown("### 上传文件")
                file_type = gr.Radio([("本地 PDF", "File"), ("PDF 链接", "Link")], value="File", show_label=False)
                upload = gr.File(label="上传或拖入 PDF", file_types=[".pdf"], type="filepath")
                link = gr.Textbox(label="PDF 下载链接", visible=False, placeholder="https://…/paper.pdf")
                service = gr.Dropdown([(SERVICE_LABELS.get(s, s), s) for s in engine.enabled_services], value=saved_service, label="翻译服务", filterable=False)
                values = [gr.Textbox(**{k: v for k, v in update.items() if k != "__type__"}) for update in provider_fields(service_map, saved_service)]
                with gr.Row():
                    choices = [(LANGUAGE_LABELS.get(name, name), name) for name in engine.lang_map]
                    lang_from = gr.Dropdown(choices, value=ConfigManager.get("PDF2ZH_LANG_FROM", "English"), label="原文语言", min_width=125, filterable=False)
                    lang_to = gr.Dropdown(choices, value=ConfigManager.get("PDF2ZH_LANG_TO", "Simplified Chinese"), label="翻译为", min_width=125, filterable=False)
                page_range = gr.Radio([("全部", "All"), ("首页", "First"), ("前五页", "First 5 pages"), ("指定页码", "Others")], value="All", label="翻译页数")
                pages = gr.Textbox(label="页码", placeholder="例如 1-5,8,10", visible=False)
                with gr.Row():
                    start = gr.Button("开始翻译", variant="primary", scale=2)
                    cancel = gr.Button("取消", visible=False, scale=1)
                save = gr.Button("保存服务配置", size="sm")
                settings_status = gr.Markdown("")
                with gr.Accordion("关于此工具", open=False):
                    gr.Markdown("使用 [cc-pdf2zh-skill](https://github.com/haorantang1999/cc-pdf2zh-skill) 对应的 [PDFMathTranslate](https://github.com/PDFMathTranslate/PDFMathTranslate) 1.9.11 引擎。本机界面加入配置保存、对照阅读、图片文字识别与排版修复。\n\n免费服务无需 API Key；选择 DeepSeek 或智谱 GLM 后填写自己的 Key 即可。图片文字识别需要额外时间。")
            with gr.Column(scale=3, min_width=450, elem_id="reading-panel"):
                with gr.Row():
                    mode = gr.Radio(READING_MODES[:1], value="source", label="阅读模式", elem_id="reading-mode", scale=4)
                    download = gr.DownloadButton("下载当前版本", visible=False, scale=1, min_width=150)
                    expand = gr.Button("展开阅读区", scale=1, min_width=120)
                status = gr.Markdown("", elem_id="reading-status")
                preview = PDF(label="文档预览", height=850, interactive=False, elem_id="reading-preview")

        reading_outputs = [files, preview, mode, download, status]
        upload.upload(source_preview, upload, reading_outputs)
        upload.clear(lambda: source_preview(None), outputs=reading_outputs)
        file_type.change(select_input, [file_type, upload], [upload, link, *reading_outputs], queue=False)
        mode.change(preview_mode, [mode, files], [preview, download], queue=False)
        expand.click(toggle_reading, reading_focus, [reading_focus, settings_panel, expand], queue=False)
        page_range.change(lambda selection: gr.update(visible=selection == "Others"), page_range, pages, queue=False)
        service.change(lambda name: provider_fields(service_map, name), service, values, queue=False)
        save.click(lambda name, source, target, *items: save_settings(service_map, name, source, target, *items), [service, lang_from, lang_to, *values], settings_status)
        beginning = start.click(begin_translation, job_state,
            outputs=[start, cancel, upload, link, status], queue=False)
        running = beginning.then(run_translation,
            [file_type, upload, link, service, lang_from, lang_to, page_range, pages, job_state, *values], reading_outputs)
        running.then(finish_controls, job_state, [start, cancel, upload, link, status], queue=False)
        cancel.click(cancel_translation, job_state, status, queue=False)
    return demo

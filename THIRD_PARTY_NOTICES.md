# 第三方软件与资源说明

本项目是社区桌面集成，并非 PDFMathTranslate 官方发行版。第三方项目的版权、商标及许可归其原权利人所有。本文件帮助定位来源；许可证原文以 `licenses/` 中保留的文件及各依赖发行包中的通知为准。

## 主要组成

| 项目或资源 | 本发行版使用情况 | 随包许可及来源 |
| --- | --- | --- |
| [PDFMathTranslate / pdf2zh](https://github.com/PDFMathTranslate/PDFMathTranslate) 1.9.11 | PDF 翻译引擎；Python 源码位于 `upstream/pdf2zh` | AGPL-3.0，`licenses/pdf2zh/licenses/LICENSE` |
| [BabelDOC](https://github.com/funstory-ai/BabelDOC) 0.2.33 | 版面分析相关依赖；Python 源码位于 `upstream/babeldoc` | AGPL-3.0，`licenses/BabelDOC/licenses/LICENSE` |
| [PyMuPDF](https://github.com/pymupdf/PyMuPDF) / [MuPDF](https://mupdf.com/) 1.25.2 | PDF 解析、绘制及 OCR 接口 | 当前随包使用 AGPL-3.0，`licenses/PyMuPDF/COPYING`；对应源码归档随 Releases 的完整源码包提供 |
| [DocLayout-YOLO](https://github.com/opendatalab/DocLayout-YOLO) | `doclayout_yolo_docstructbench_imgsz1024.onnx` 文档版面模型 | 所收集上游项目许可为 AGPL-3.0，`licenses/DocLayout-YOLO-LICENSE.txt` |
| [RapidOCR](https://github.com/RapidAI/RapidOCR) / rapidocr-onnxruntime 1.4.4 | 本地文字检测与识别 | RapidOCR 项目代码采用 Apache-2.0，`licenses/RapidOCR-Apache-2.0.txt`；具体权重说明见下文 |
| English PP-OCRv4 识别模型 | `en_PP-OCRv4_rec_mobile.onnx`，由 RapidAI 分发 | 来源链接见 `app/assets/OCR-MODEL.md`；随包保留 RapidOCR 项目许可 |
| [Tesseract tessdata_fast](https://github.com/tesseract-ocr/tessdata_fast) | `tessdata/eng.traineddata` 英文 OCR 数据 | Apache-2.0，`licenses/Tesseract-Apache-2.0.txt` |
| [Adobe Source Han Serif](https://github.com/adobe-fonts/source-han-serif) | `SourceHanSerifCN-Regular.ttf` 中文字体 | SIL Open Font License 1.1，`licenses/SourceHanSerif-OFL.txt`；保留字体名称与版权通知 |
| [Python](https://www.python.org/) 3.12 | Windows 打包运行环境 | PSF 及原发行版包含的许可，`licenses/Python-3.12-LICENSE.txt` |
| [PyInstaller](https://pyinstaller.org/) 6.22.3 | 冻结打包工具与 bootloader | GPL-2.0-or-later 及 bootloader exception，`licenses/PyInstaller-license-and-bootloader-exception.txt` |
| [Gradio](https://github.com/gradio-app/gradio) 5.35.0 | 本机浏览器界面 | Apache-2.0，`licenses/gradio/licenses/LICENSE` |

其余 Python 依赖及版本列于 `requirements.txt`，已收集到的各发行包许可证与 NOTICE 放在 `licenses/` 的对应目录。保留这些文件并不改变其原有条款。

## 源码和二进制的分发

社区桌面模块与本地修改按仓库根目录的 `LICENSE`（AGPL-3.0）提供。上游 Python 源码保留在 `upstream/`，当前使用的完整 PyMuPDF/MuPDF 大型源码归档随 Releases 的 `源码与许可证.zip` 提供。Windows 安装包同时携带该完整源码与许可证归档；继续分享安装包时应保留它。

模型、字体及运行环境体积较大，随 Release 提供，不存入 Git 源码历史。仓库内的 CS5489 示例 PDF 及其截图仅展示实际翻译效果，原课程资料的标题、日期、署名和其他原标识保留，教学内容版权仍归原作者，不纳入本项目的代码授权。

## 当前许可材料的范围

原发行档案未收集到以下 7 项的独立许可证。2026-10-17 已在本仓库补入真实原文及来源记录；其中 ONNX Runtime 和 tqdm 的原文直接取自对应版本 wheel，其余按下表注明上游出处。`rapidocr-onnxruntime` 的项目代码许可仍以顶层 RapidOCR 许可文件提供。

| 依赖版本 | 补入材料 | 来源范围 |
| --- | --- | --- |
| ffmpy 1.0.0 | `licenses/ffmpy/LICENSE` | 官方上游 `1.0.0` tag；[来源记录](licenses/ffmpy/SOURCE.md) |
| flatbuffers 25.12.19 | `licenses/flatbuffers/LICENSE` | 官方上游 `v25.12.19` tag；[来源记录](licenses/flatbuffers/SOURCE.md) |
| gradio_client 1.10.4 | `licenses/gradio_client/LICENSE` | 官方上游 `gradio_client@1.10.4` tag；[来源记录](licenses/gradio_client/SOURCE.md) |
| onnxruntime 1.30.0 | `licenses/onnxruntime/LICENSE`、`ThirdPartyNotices.txt` | 原 Windows wheel 内的项目许可和第三方通知；[来源记录](licenses/onnxruntime/SOURCE.md) |
| tencentcloud-sdk-python-common 3.1.178 | `licenses/tencentcloud-sdk-python-common/LICENSE`、`COPYRIGHT-NOTICE.txt` | 官方上游项目 LICENSE；精确版本源码包内的版权及 Apache-2.0 声明；[来源记录](licenses/tencentcloud-sdk-python-common/SOURCE.md) |
| tencentcloud-sdk-python-tmt 3.1.70 | `licenses/tencentcloud-sdk-python-tmt/LICENSE`、`COPYRIGHT-NOTICE.txt` | 官方上游项目 LICENSE；精确版本源码包内的版权及 Apache-2.0 声明；[来源记录](licenses/tencentcloud-sdk-python-tmt/SOURCE.md) |
| tqdm 4.70.1 | `licenses/tqdm/LICENCE`、`MPL-2.0.txt` | 原 wheel 内的 MPL-2.0 AND MIT 通知及 Mozilla 官方 MPL 完整原文；[来源记录](licenses/tqdm/SOURCE.md) |

两项 Tencent SDK 的精确版本 wheel 和源码包均未附独立 LICENSE/NOTICE；补入的完整 LICENSE 取自官方仓库 `master` 分支，并保留精确版本源码包内的版权许可头。该出处边界已写入各自 `SOURCE.md`，不宣称它们原本包含该独立文件。

这些补充材料同时另存为 Release 附件 `additional-third-party-notices.zip`，与现有安装器一起提供；现有 EXE 内部的许可档案不因这次补充而改变。

实际使用的 `SourceHanSerifCN-Regular.ttf` 取自 [BabelDOC-Assets 字体目录](https://github.com/funstory-ai/BabelDOC-Assets/tree/main/fonts)，随包保留 Adobe Source Han Serif 的 OFL 原文及其字体名称、版权通知。DocLayout ONNX 文件取自 [AI-ModelScope 的 DocLayout-YOLO-DocStructBench-onnx](https://www.modelscope.cn/models/AI-ModelScope/DocLayout-YOLO-DocStructBench-onnx)，英文 PP-OCRv4 文件取自 `app/assets/OCR-MODEL.md` 中记录的 RapidAI 分发地址。

当前材料没有为每个具体 ONNX 权重另存独立模型卡或权重授权文件；DocLayout-YOLO 的 AGPL 与 RapidOCR 的 Apache 文件均为上游项目代码许可材料。本文件不把项目代码许可自动等同于所有模型权重的许可。

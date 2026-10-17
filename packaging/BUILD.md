# 从源码构建 Windows 安装包

本项目是基于 PDFMathTranslate 1.9.11 的非官方社区桌面版。以下命令在仓库根目录的 PowerShell 中执行，使用 Windows x64 与 Python 3.12。构建脚本的路径均相对本仓库，不依赖制作者电脑上的 Python、Codex 或用户目录。

## 1. 准备 Python 依赖与本地上游源码

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install PyInstaller==6.22.3
Copy-Item .\upstream\pdf2zh\* -Destination .\.venv\Lib\site-packages\pdf2zh -Recurse -Force
Copy-Item .\upstream\babeldoc\* -Destination .\.venv\Lib\site-packages\babeldoc -Recurse -Force
```

`requirements.txt` 是当前发行环境的完整版本快照，包含间接依赖和少量环境工具，并非经过删减的最小依赖表。覆盖上游源码的步骤用于构建当前随包提供的本地版本，同时保留已安装包的元数据与其他资源。

## 2. 准备模型、字体与 OCR 资源

Git 仓库不包含大型模型和字体。可先安装 [Releases](https://github.com/zhang-yutong123/paperbridge/releases/latest) 中的当前 Windows 安装包，从安装目录的 `_internal/assets` 复制资源到仓库的 `app/assets`。需要的结构为：

```text
app/assets/
  default-settings.json                      仓库提供的无 Key 默认设置
  prompt.txt                                 仓库提供的默认提示词
  OCR-MODEL.md                                英文识别模型来源
  doclayout_yolo_docstructbench_imgsz1024.onnx
  en_PP-OCRv4_rec_mobile.onnx
  SourceHanSerifCN-Regular.ttf
  tessdata/
    eng.traineddata
```

当前版本的安装目录为 `%LOCALAPPDATA%\Programs\PDF2ZH\2026.10.17.1`。RapidOCR 自带模型位于已安装程序的 `_internal/rapidocr_onnxruntime/models`；如果重建环境所安装的 RapidOCR 包未带齐模型，可把该目录复制到 `.venv/Lib/site-packages/rapidocr_onnxruntime/models`。PyInstaller 会通过 `collect_all` 收入 RapidOCR 包内资源。

不要从日常数据目录复制 `settings.json`、Key、缓存或历史 PDF。模型和字体的来源与许可证见 `../THIRD_PARTY_NOTICES.md`。

## 3. 准备对应的第三方源码归档

完整发行包会附带 `源码与许可证.zip`，其中包含桌面模块、本地 `pdf2zh` / `babeldoc` 源码、构建脚本、依赖版本和许可证，以及 PyMuPDF / MuPDF 的源码归档。

优先从 Releases 的 `源码与许可证.zip` 中取出 `upstream-source/` 下的以下两份归档，放入 `packaging/source-archives/`：

- `pymupdf-1.25.2.tar.gz`
- `mupdf-1.25.2-source.tar.gz`

也可以运行现有下载脚本，从脚本中列出的官方源获取归档与相关资源的许可证：

```powershell
.\.venv\Scripts\python.exe .\packaging\fetch_sources.py
```

上述大归档只作为 Release 附件分发，不进入 Git 历史。`licenses/` 保存当前随包收集的许可证；涉及更新依赖或发布二进制时，应同时提供相应发行包要求的许可证、NOTICE 与必要源码。

## 4. 构建程序与安装器

```powershell
.\packaging\build.ps1
$env:PDF2ZH_BUILD_PACKAGES = (Resolve-Path .\.venv\Lib\site-packages).Path
.\.venv\Scripts\python.exe .\packaging\package_release.py
```

输出为：

```text
release/
  PDF论文翻译-安装.exe
  源码与许可证.zip
  程序/PDF论文翻译/
```

`build.ps1` 使用 PyInstaller 生成目录形式的 Windows 程序。`package_release.py` 把程序和完整源码归档打进安装器，通过 Windows 自带的 .NET Framework x64 C# 编译器编译 `installer.cs`。使用者运行安装器时，会解压到当前用户的 LocalAppData 并创建桌面快捷方式，无需另装开发环境。

## 版本和运行选项

发布更新时同时修改 `app/desktop_ui.py` 显示的版本和 `packaging/installer.cs` 的安装目录版本，然后重新构建。同版本安装已完成时会复用现有安装目录；只改开发源码不会更新已安装的 EXE。

默认监听 `127.0.0.1:7868`。`PDF2ZH_PORT` 可更改端口，`PDF2ZH_DATA_DIR` 可指定可写的数据目录；EXE 的 `--no-browser` 参数可启动服务而不弹出浏览器和启动窗口。

当前发行包未做商业代码签名，Windows 的发布者提示及 Smart App Control 限制也适用于自行构建的未签名安装器。

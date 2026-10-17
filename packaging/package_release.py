"""Create a clean, redistributable installer and its corresponding sources."""
from pathlib import Path
import importlib.metadata as metadata
import os
import shutil
import subprocess
import zipfile


ROOT = Path(__file__).resolve().parent.parent
PACKAGING = ROOT / "packaging"
APP = ROOT / "app"
RELEASE = ROOT / "release"
PROGRAM = RELEASE / "程序" / "PDF论文翻译"
PACKAGES = Path(os.environ["PDF2ZH_BUILD_PACKAGES"])


def zip_sources():
    archive_path = PROGRAM / "源码与许可证.zip"
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in ["README.md", "LICENSE", "THIRD_PARTY_NOTICES.md", "upstream/MODIFICATIONS.md"]:
            archive.write(ROOT / name, name)
        for path in sorted(APP.glob("*.py")):
            archive.write(path, "app/" + path.name)
        for path in sorted((APP / "vendor").glob("*.py")):
            archive.write(path, "app/vendor/" + path.name)
        for path in sorted(PACKAGING.glob("*")):
            if path.suffix in {".py", ".ps1", ".spec", ".cs", ".md"} and not path.name.startswith("probe_"):
                archive.write(path, "packaging/" + path.name)
        for name in ["prompt.txt", "default-settings.json", "OCR-MODEL.md"]:
            archive.write(APP / "assets" / name, "app/assets/" + name)
        for package in ["pdf2zh", "babeldoc"]:
            for path in sorted((PACKAGES / package).rglob("*.py")):
                archive.write(path, "upstream/" + package + "/" + str(path.relative_to(PACKAGES / package)))
        for path in sorted((ROOT / "licenses").rglob("*")):
            if path.is_file():
                archive.write(path, "licenses/" + str(path.relative_to(ROOT / "licenses")))
        for path in sorted((PACKAGING / "source-archives").glob("*.tar.gz")):
            archive.write(path, "upstream-source/" + path.name)
        archive.writestr("requirements.txt", "\n".join(sorted(
            distribution.metadata["Name"] + "==" + distribution.version
            for distribution in metadata.distributions(path=[str(PACKAGES)]))) + "\n")
    shutil.copy2(archive_path, RELEASE / archive_path.name)


def main():
    licenses = ROOT / "licenses"
    licenses.mkdir(exist_ok=True)
    for distribution in metadata.distributions(path=[str(PACKAGES)]):
        name = distribution.metadata["Name"]
        info = Path(distribution._path)
        target = licenses / name
        for path in info.rglob("*"):
            if path.is_file() and ("license" in path.name.lower() or "copying" in path.name.lower() or "notice" in path.name.lower()):
                destination = target / path.relative_to(info)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, destination)
    shutil.copy2(PACKAGING / "分享说明.md", PROGRAM / "分享说明.md")
    zip_sources()
    payload = PACKAGING / "build" / "payload.zip"
    with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(PROGRAM.rglob("*")):
            if path.is_file():
                archive.write(path, str(path.relative_to(PROGRAM)))
    compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"
    subprocess.run([str(compiler), "/nologo", "/target:winexe", "/platform:x64", "/optimize+",
        "/reference:System.Windows.Forms.dll", "/reference:System.Drawing.dll",
        "/reference:System.IO.Compression.dll", "/reference:System.IO.Compression.FileSystem.dll",
        "/reference:Microsoft.CSharp.dll", "/resource:" + str(payload) + ",PDF2ZH.Payload",
        "/out:" + str(RELEASE / "PDF论文翻译-安装.exe"), str(PACKAGING / "installer.cs")], check=True)
    print("Installer created:", RELEASE / "PDF论文翻译-安装.exe")


if __name__ == "__main__":
    main()

"""Build a Windows installer from a PyInstaller directory build."""
from pathlib import Path
import argparse
import os
import subprocess
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
SETUP = ROOT / "tools/installer"
ASSETS = ROOT / "app/assets"
PRODUCT = "伯爵大光圈模拟"


def build(payload: Path, output: Path, model: Path | None, license_file: Path | None):
    if not (payload / f"{PRODUCT}.exe").is_file() or not (payload / "_internal").is_dir():
        raise ValueError("安装包输入必须是完整的程序目录版")
    if output.exists():
        raise FileExistsError(output)
    if model and not license_file:
        raise ValueError("随附模型时须提供原始许可证")
    compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name(output.stem + ".pending.exe")
    subprocess.run([
        compiler, "/nologo", "/target:winexe", "/platform:x64", "/optimize+",
        "/codepage:65001", "/reference:System.Windows.Forms.dll",
        "/reference:System.Drawing.dll", "/reference:System.IO.Compression.dll",
        f"/win32manifest:{SETUP / 'app.manifest'}",
        f"/win32icon:{ASSETS / 'brand/app.ico'}",
        f"/resource:{ASSETS / 'fonts/SarasaGothicSC-Bold.ttf'},sarasa.ttf",
        f"/resource:{ASSETS / 'completion/illustration.png'},illustration.png",
        f"/out:{partial}", SETUP / "Setup.cs",
    ], check=True)
    with ZipFile(partial, "a", ZIP_DEFLATED, compresslevel=3) as zip_file:
        zip_file.write(payload / f"{PRODUCT}.exe", f"{PRODUCT}.exe")
        for source in sorted((payload / "_internal").rglob("*")):
            if source.is_file():
                zip_file.write(source, source.relative_to(payload).as_posix())
        if model:
            model_root = "models/depth-anything--Depth-Anything-V2-Small-hf/"
            for name in ("config.json", "preprocessor_config.json", "model.safetensors"):
                zip_file.write(model / name, model_root + name)
            zip_file.write(license_file, model_root + "LICENSE")
            zip_file.writestr(model_root + "SOURCE.txt",
                              "https://huggingface.co/depth-anything/Depth-Anything-V2-Small-hf\n")
    with ZipFile(partial) as zip_file:
        bad = zip_file.testzip()
        if bad:
            raise ValueError(f"安装包校验失败：{bad}")
    partial.rename(output)
    print(f"{output} ({output.stat().st_size / 1024 ** 2:.0f} MiB)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("payload", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--model", type=Path)
    parser.add_argument("--model-license", type=Path)
    args = parser.parse_args()
    build(args.payload.resolve(), args.output.resolve(), args.model, args.model_license)

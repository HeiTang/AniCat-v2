import sys
from importlib.metadata import version
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, copy_metadata, get_package_paths

root = Path(SPECPATH).resolve().parents[1]
imageio_path = Path(get_package_paths("imageio_ffmpeg")[1])
ffmpeg, = (imageio_path / "binaries").glob("ffmpeg-*")
windows = sys.platform == "win32"
icon = root / "website/assets/anicat-logo.png"

analysis = Analysis(
    [str(Path(SPECPATH) / "launch.py")],
    binaries=[(str(ffmpeg), "imageio_ffmpeg/binaries")],
    datas=[
        (str(root / "LICENSE"), "."),
        *collect_data_files("anicat", includes=["assets/*.png"]),
        *copy_metadata("anicat"),
        *copy_metadata("imageio-ffmpeg"),
    ],
)
executable = EXE(
    PYZ(analysis.pure),
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="AniCat",
    console=False,
    icon=str(icon) if windows else None,
    manifest=str(Path(SPECPATH) / "windows.manifest") if windows else None,
    upx=False,
)
app = COLLECT(executable, analysis.binaries, analysis.datas, name="AniCat", upx=False)

if sys.platform == "darwin":
    app = BUNDLE(
        app,
        name="AniCat.app",
        icon=str(icon),
        bundle_identifier="io.github.heitang.anicat",
        version=version("anicat"),
    )

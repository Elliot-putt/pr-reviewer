# PyInstaller spec — builds "PR Reviewer.app"
# Build: uv run --with pyinstaller pyinstaller pr-reviewer.spec --noconfirm
import re
from pathlib import Path

APP_VERSION = re.search(
    r'__version__ = "([^"]+)"', Path("src/prreviewer/version.py").read_text()
).group(1)

block_cipher = None

a = Analysis(
    ["main.py"],
    pathex=["src"],
    datas=[("src/prreviewer/ui/assets", "assets")],
    hiddenimports=[
        "prreviewer",
        "slack_bolt",
        "slack_bolt.adapter.socket_mode",
        "slack_sdk",
        "github",
        "ptyprocess",
        "websockets",
        "aiohttp",
        "pydantic_settings",
        "requests",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="PR Reviewer",
    console=False,
    icon="build_res/icon.icns",
)

coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, name="PR Reviewer")

app = BUNDLE(
    coll,
    name="PR Reviewer.app",
    icon="build_res/icon.icns",
    bundle_identifier="dev.elliotputt.pr-reviewer",
    version=APP_VERSION,
    info_plist={
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "12.0",
        "CFBundleShortVersionString": APP_VERSION,
    },
)

# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 빌드 설정.  실행:  pyinstaller pdfeditor.spec --noconfirm

결과물: dist/PDFEditor.exe (Windows) 또는 dist/PDFEditor (macOS/Linux) 단일 실행 파일.
"""
import os
import sys

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

block_cipher = None

datas = [("pdfeditor/static", "pdfeditor/static")]
binaries = []
hiddenimports = ["pdfeditor", "pdfeditor.core", "pdfeditor.app", "pdfeditor.desktop", "pdfeditor.cli"]

# PDF 엔진 (MuPDF 바이너리 + 내장 CJK 폰트 포함)
for pkg in ("pymupdf",):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# 자체 창 (pywebview) — WebView2 로더 DLL 등 데이터 포함
try:
    datas += collect_data_files("webview")
    hiddenimports += collect_submodules("webview")
    if sys.platform == "win32":
        for pkg in ("clr_loader", "pythonnet"):
            try:
                d, b, h = collect_all(pkg)
                datas += d
                binaries += b
                hiddenimports += h
            except Exception:
                pass
        hiddenimports += ["clr", "webview.platforms.winforms", "webview.platforms.edgechromium"]
except Exception:
    pass  # pywebview 가 없으면 브라우저 모드로만 동작

hiddenimports += ["flask", "jinja2", "werkzeug", "bottle"]

icon = "assets/icon.ico" if os.path.exists("assets/icon.ico") else None

a = Analysis(
    ["desktop_main.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # cryptography/OpenSSL 은 werkzeug 의 선택적 SSL 기능에만 쓰이며 이 프로그램에는 불필요
    excludes=["tkinter", "pytest", "playwright", "PIL.ImageQt", "PyQt5", "PyQt6", "PySide2", "PySide6", "cryptography", "OpenSSL"],
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="PDFEditor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,  # 검은 명령창 없이 실행
    disable_windowed_traceback=False,
    icon=icon,
)

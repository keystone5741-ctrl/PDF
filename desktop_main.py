#!/usr/bin/env python3
"""PDF 편집기 데스크톱 창 모드 진입점 (PyInstaller 빌드 대상).

    python desktop_main.py [파일.pdf]
    PDFEditor.exe [파일.pdf]
    PDFEditor.exe --selftest     # 번들 자가 진단 (CI)
"""
import multiprocessing
import sys

from pdfeditor.desktop import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())

"""데스크톱 창 모드.

브라우저 대신 자체 창(pywebview, Windows 에서는 Edge WebView2)을 띄워 일반 프로그램처럼 실행합니다.
pywebview 를 쓸 수 없는 환경이면 기본 브라우저로 대신 엽니다.

    python desktop_main.py [파일.pdf]
    python -m pdfeditor desktop [파일.pdf]
"""

from __future__ import annotations

import logging
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

from .app import create_app

APP_TITLE = "PDF 편집기"
LOG_NAME = "pdf-editor.log"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _setup_logging() -> Path | None:
    """창 모드(콘솔 없음)에서는 stdout/stderr 가 None 이므로 로그 파일로 돌립니다."""
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    if sys.stdout is not None and sys.stderr is not None:
        return None
    log_path = Path(tempfile.gettempdir()) / LOG_NAME
    try:
        f = open(log_path, "a", encoding="utf-8", buffering=1)
    except OSError:
        f = open(os.devnull, "w")
    if sys.stdout is None:
        sys.stdout = f
    if sys.stderr is None:
        sys.stderr = f
    return log_path


def _start_server(initial_file: str | None) -> str:
    port = _free_port()
    app = create_app(initial_file)
    thread = threading.Thread(
        target=lambda: app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False, threaded=True),
        daemon=True,
    )
    thread.start()
    url = f"http://127.0.0.1:{port}/"
    for _ in range(100):  # 최대 10초 대기
        try:
            urllib.request.urlopen(url, timeout=0.5).read(1)
            break
        except Exception:
            time.sleep(0.1)
    return url


class Api:
    """화면(JavaScript)에서 window.pywebview.api 로 부르는 기능. Windows 기본 대화상자를 제공합니다."""

    def __init__(self) -> None:
        self.window = None

    @staticmethod
    def _first(result) -> str | None:
        if not result:
            return None
        if isinstance(result, (list, tuple)):
            return str(result[0]) if result else None
        return str(result)

    def pick_open(self) -> str | None:
        import webview

        r = self.window.create_file_dialog(webview.OPEN_DIALOG, file_types=("PDF 파일 (*.pdf)", "모든 파일 (*.*)"))
        return self._first(r)

    def pick_save(self, default_name: str = "document.pdf", directory: str = "") -> str | None:
        import webview

        r = self.window.create_file_dialog(
            webview.SAVE_DIALOG, directory=directory or "", save_filename=default_name, file_types=("PDF 파일 (*.pdf)",)
        )
        path = self._first(r)
        if path and not path.lower().endswith(".pdf"):
            path += ".pdf"
        return path

    def pick_folder(self) -> str | None:
        import webview

        return self._first(self.window.create_file_dialog(webview.FOLDER_DIALOG))

    def reveal(self, path: str) -> bool:
        """저장한 파일이 있는 폴더를 탐색기로 엽니다."""
        try:
            p = Path(path)
            if sys.platform == "win32":
                subprocess.Popen(["explorer", "/select,", str(p)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", str(p)])
            else:
                subprocess.Popen(["xdg-open", str(p.parent)])
            return True
        except Exception:
            return False

    def is_desktop(self) -> bool:
        return True


def selftest() -> int:
    """빌드된 실행 파일이 제대로 동작하는지 확인 (CI 용). 서버를 띄우고 페이지·PDF 렌더링을 점검합니다."""
    from .core import PDFEditor

    url = _start_server(None)
    html = urllib.request.urlopen(url, timeout=5).read().decode("utf-8")
    assert "PDF 편집기" in html, "index.html 이 번들에 없습니다"
    ed = PDFEditor()
    ed.add_text(0, 72, 72, "자가 진단 한글 テスト")
    assert "자가 진단" in ed.get_page_text(0)
    assert ed.render_page(0, 0.5)[:4] == b"\x89PNG"
    print("selftest ok:", url)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--selftest" in argv:
        return selftest()
    log_path = _setup_logging()
    initial = next((a for a in argv if a.lower().endswith(".pdf") and Path(a).is_file()), None)
    url = _start_server(initial)

    try:
        import webview
    except Exception:  # pywebview 없음 → 브라우저로 대체
        print(f"{APP_TITLE}: 창 라이브러리를 찾을 수 없어 브라우저로 엽니다. {url}")
        webbrowser.open(url)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            return 0

    api = Api()
    window = webview.create_window(
        APP_TITLE,
        url,
        js_api=api,
        width=1280,
        height=860,
        min_size=(900, 600),
        text_select=True,
    )
    api.window = window
    try:
        webview.start()
    except Exception as e:  # WebView2 런타임이 없는 등 창을 못 띄우면 브라우저로 대체
        print(f"{APP_TITLE}: 창을 열지 못했습니다 ({e}). 브라우저로 엽니다.")
        if log_path:
            print(f"로그 파일: {log_path}")
        webbrowser.open(url)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            return 0
    # 창이 닫히면 서버 스레드와 함께 즉시 종료
    os._exit(0)


if __name__ == "__main__":
    sys.exit(main())

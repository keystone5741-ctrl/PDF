"""브라우저 기반 PDF 편집기 서버 (Flask).

실행:  python main.py  또는  python -m pdfeditor
"""

from __future__ import annotations

import io
import json
import tempfile
import threading
import time
import uuid
import webbrowser
from collections import OrderedDict
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_file, send_from_directory

from .core import PDFEditor, parse_page_range

STATIC_DIR = Path(__file__).parent / "static"


def create_app(initial_file: str | None = None, recovery_dir: str | Path | None = None, autosave_delay: float = 4.0) -> Flask:
    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = 512 * 1024 * 1024  # 512MB
    docs: dict[str, PDFEditor] = {}
    names: dict[str, str] = {}
    lock = threading.Lock()
    # PyMuPDF 는 스레드 안전하지 않으므로 /api/ 요청은 한 번에 하나씩만 처리합니다.
    api_lock = threading.RLock()
    # 렌더링 결과 캐시: (doc_id, 페이지 키, zoom) → PNG. 페이지 내용이 바뀌면 키가 달라져 자연히 무효화됩니다.
    # 개수와 총 용량(바이트) 둘 다 제한해 큰 페이지에서도 메모리가 불어나지 않게 합니다.
    render_cache: OrderedDict[tuple, bytes] = OrderedDict()
    RENDER_CACHE_MAX = 80
    RENDER_CACHE_BYTES = 64 * 1024 * 1024
    cache_bytes = [0]

    # 복구용 자동 저장: 마지막 변경 후 잠시 뒤 임시 폴더에 문서를 통째로 써 둡니다.
    RECOVERY_DIR = Path(recovery_dir) if recovery_dir else Path(tempfile.gettempdir()) / "pdf-editor-recovery"
    AUTOSAVE_DELAY = float(autosave_delay)
    dirty: dict[str, float] = {}  # doc_id → 마지막 변경 시각
    autosave_stop = threading.Event()

    def recovery_paths(doc_id: str) -> tuple[Path, Path]:
        return RECOVERY_DIR / f"{doc_id}.pdf", RECOVERY_DIR / f"{doc_id}.json"

    def write_recovery(doc_id: str) -> None:
        ed = docs.get(doc_id)
        if ed is None:
            return
        RECOVERY_DIR.mkdir(parents=True, exist_ok=True)
        pdf_path, meta_path = recovery_paths(doc_id)
        tmp = pdf_path.with_suffix(".tmp")
        tmp.write_bytes(ed.doc.tobytes(deflate=True))
        tmp.replace(pdf_path)
        meta_path.write_text(
            json.dumps({"name": names.get(doc_id, ""), "path": str(ed.path) if ed.path else None, "time": time.time(), "pages": ed.page_count}, ensure_ascii=False),
            encoding="utf-8",
        )

    def drop_recovery(doc_id: str) -> None:
        for path in recovery_paths(doc_id):
            try:
                path.unlink()
            except FileNotFoundError:
                pass

    def autosave_loop() -> None:
        while not autosave_stop.wait(1.0):
            now = time.time()
            due = [d for d, t in list(dirty.items()) if now - t >= AUTOSAVE_DELAY]
            for doc_id in due:
                with api_lock:
                    if dirty.get(doc_id, now) > now - AUTOSAVE_DELAY:
                        continue  # 그 사이 또 바뀜 → 다음 기회에
                    try:
                        write_recovery(doc_id)
                        dirty.pop(doc_id, None)
                    except Exception:
                        dirty.pop(doc_id, None)

    threading.Thread(target=autosave_loop, daemon=True, name="pdf-autosave").start()
    app.config["AUTOSAVE_STOP"] = autosave_stop
    app.config["RECOVERY_DIR"] = RECOVERY_DIR

    def list_recoveries() -> list[dict]:
        out = []
        if not RECOVERY_DIR.is_dir():
            return out
        for meta_path in RECOVERY_DIR.glob("*.json"):
            rid = meta_path.stem
            pdf_path = RECOVERY_DIR / f"{rid}.pdf"
            if rid in docs or not pdf_path.is_file():
                continue  # 지금 열려 있는 문서의 복구본은 목록에서 제외
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            out.append({"id": rid, "name": meta.get("name") or "문서.pdf", "path": meta.get("path"), "time": meta.get("time", 0), "pages": meta.get("pages")})
        out.sort(key=lambda m: -m["time"])
        return out

    @app.before_request
    def _acquire():
        if request.path.startswith("/api/"):
            api_lock.acquire()
            request.environ["pdfeditor.locked"] = True

    @app.after_request
    def _mark_dirty(response):
        # 문서를 바꾸는 요청이 성공하면 복구용 자동 저장 예약
        if request.method == "POST" and response.status_code == 200 and request.path.startswith("/api/doc/"):
            parts = request.path.split("/")
            if len(parts) > 3 and parts[3] in docs and not request.path.endswith(("/extract", "/extract_each", "/save", "/close")):
                dirty[parts[3]] = time.time()
        return response

    @app.teardown_request
    def _release(exc):
        if request.environ.pop("pdfeditor.locked", False):
            api_lock.release()

    def cache_drop_doc(doc_id: str) -> None:
        for key in [k for k in render_cache if k[0] == doc_id]:
            cache_bytes[0] -= len(render_cache.pop(key))

    def cached_render(doc_id: str, ed: PDFEditor, page: int, zoom: float) -> bytes:
        key = (doc_id, ed.page_key(page), round(zoom, 3))
        png = render_cache.get(key)
        if png is None:
            png = ed.render_page(page, zoom)
            render_cache[key] = png
            cache_bytes[0] += len(png)
            while render_cache and (len(render_cache) > RENDER_CACHE_MAX or cache_bytes[0] > RENDER_CACHE_BYTES):
                _, old = render_cache.popitem(last=False)
                cache_bytes[0] -= len(old)
        else:
            render_cache.move_to_end(key)
        return png

    def register(editor: PDFEditor, name: str) -> str:
        """문서를 등록합니다. 화면은 한 번에 문서 하나만 다루므로 이전 문서는 닫아 메모리를 돌려줍니다."""
        doc_id = uuid.uuid4().hex[:12]
        with lock:
            old_ids = list(docs)
            docs[doc_id] = editor
            names[doc_id] = name
        for old in old_ids:
            ed = docs.pop(old, None)
            names.pop(old, None)
            dirty.pop(old, None)
            cache_drop_doc(old)
            if ed is not None:
                try:
                    ed.close()
                except Exception:
                    pass
        return doc_id

    def get_doc(doc_id: str) -> PDFEditor:
        ed = docs.get(doc_id)
        if ed is None:
            abort(404, "문서를 찾을 수 없습니다. 파일을 다시 열어 주세요.")
        return ed

    def doc_state(doc_id: str) -> dict:
        ed = get_doc(doc_id)
        return {
            "id": doc_id,
            "name": names[doc_id],
            "path": str(ed.path) if ed.path else None,
            "pages": ed.page_info(),
            "page_count": ed.page_count,
            "can_undo": ed.can_undo,
            "can_redo": ed.can_redo,
        }

    if initial_file:
        app.config["INITIAL_DOC"] = register(PDFEditor(initial_file), Path(initial_file).name)

    # ---------------------------------------------------------------- 정적 파일
    @app.get("/")
    def index():
        return send_from_directory(STATIC_DIR, "index.html")

    @app.get("/static/<path:filename>")
    def static_files(filename):
        return send_from_directory(STATIC_DIR, filename)

    @app.errorhandler(400)
    @app.errorhandler(404)
    @app.errorhandler(413)
    def handle_http_error(err):
        return jsonify({"error": getattr(err, "description", str(err))}), err.code

    @app.errorhandler(Exception)
    def handle_error(err):
        if hasattr(err, "code") and isinstance(err.code, int):
            return jsonify({"error": getattr(err, "description", str(err))}), err.code
        return jsonify({"error": str(err) or err.__class__.__name__}), 400

    # ---------------------------------------------------------------- 문서 열기/저장
    @app.get("/api/initial")
    def initial():
        doc_id = app.config.get("INITIAL_DOC")
        return jsonify(doc_state(doc_id) if doc_id else {})

    @app.post("/api/open")
    def open_doc():
        if "file" in request.files:
            f = request.files["file"]
            data = f.read()
            if not data:
                abort(400, "빈 파일입니다.")
            ed = PDFEditor(data)
            doc_id = register(ed, f.filename or "document.pdf")
        else:
            body = request.get_json(silent=True) or {}
            path = body.get("path")
            if not path:
                abort(400, "파일 또는 경로가 필요합니다.")
            p = Path(path).expanduser()
            if not p.is_file():
                abort(400, f"파일을 찾을 수 없습니다: {p}")
            ed = PDFEditor(p)
            doc_id = register(ed, p.name)
        return jsonify(doc_state(doc_id))

    @app.get("/api/doc/<doc_id>")
    def state(doc_id):
        return jsonify(doc_state(doc_id))

    @app.post("/api/doc/<doc_id>/close")
    def close_doc(doc_id):
        with lock:
            ed = docs.pop(doc_id, None)
            names.pop(doc_id, None)
        dirty.pop(doc_id, None)
        cache_drop_doc(doc_id)
        drop_recovery(doc_id)
        if ed:
            ed.close()
        return jsonify({"ok": True})

    # ---------------------------------------------------------------- 복구 (자동 저장본)
    @app.get("/api/recovery")
    def recovery_list():
        return jsonify({"items": list_recoveries()})

    @app.post("/api/recovery/<rid>/open")
    def recovery_open(rid):
        pdf_path, meta_path = recovery_paths(rid)
        if not pdf_path.is_file():
            abort(404, "복구본을 찾을 수 없습니다.")
        meta = {}
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            pass
        ed = PDFEditor(pdf_path.read_bytes())
        if meta.get("path"):
            ed.path = Path(meta["path"])  # '저장' 이 원래 파일 위치를 가리키도록
        doc_id = register(ed, meta.get("name") or pdf_path.name)
        drop_recovery(rid)
        dirty[doc_id] = time.time()
        return jsonify(doc_state(doc_id))

    @app.delete("/api/recovery/<rid>")
    def recovery_delete(rid):
        drop_recovery(rid)
        return jsonify({"ok": True})

    @app.get("/api/doc/<doc_id>/download")
    def download(doc_id):
        ed = get_doc(doc_id)
        name = names[doc_id]
        stem = name[:-4] if name.lower().endswith(".pdf") else name
        return send_file(
            io.BytesIO(ed.to_bytes()),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=f"{stem}_edited.pdf",
        )

    @app.post("/api/doc/<doc_id>/save")
    def save(doc_id):
        ed = get_doc(doc_id)
        body = request.get_json(silent=True) or {}
        path = body.get("path") or (str(ed.path) if ed.path else None)
        if not path:
            abort(400, "저장할 경로를 입력하세요. (파일을 업로드로 연 경우 '다운로드' 를 사용하세요)")
        target = ed.save(Path(path).expanduser())
        names[doc_id] = target.name
        dirty.pop(doc_id, None)
        drop_recovery(doc_id)  # 정식으로 저장했으니 복구본은 필요 없음
        return jsonify({"ok": True, "path": str(target)})

    # ---------------------------------------------------------------- 페이지 보기
    @app.get("/api/doc/<doc_id>/page/<int:page>/image")
    def page_image(doc_id, page):
        ed = get_doc(doc_id)
        zoom = float(request.args.get("zoom", 1.5))
        zoom = max(0.1, min(zoom, 6.0))
        png = cached_render(doc_id, ed, page, zoom)
        resp = send_file(io.BytesIO(png), mimetype="image/png")
        # v(페이지 키)가 URL 에 들어 있으면 내용이 바뀔 때 URL 도 바뀌므로 브라우저가 오래 캐시해도 안전
        if request.args.get("v"):
            resp.headers["Cache-Control"] = "private, max-age=31536000, immutable"
        else:
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.get("/api/doc/<doc_id>/page/<int:page>/blocks")
    def page_blocks(doc_id, page):
        ed = get_doc(doc_id)
        w, h = ed.page_size(page)
        merge = request.args.get("merge", "0") in ("1", "true", "yes")
        return jsonify({"width": w, "height": h, "blocks": [b.to_dict() for b in ed.get_text_blocks(page, merge_paragraphs=merge)]})

    @app.get("/api/doc/<doc_id>/page/<int:page>/text")
    def page_text(doc_id, page):
        return jsonify({"text": get_doc(doc_id).get_page_text(page)})

    # ---------------------------------------------------------------- 텍스트 편집
    @app.post("/api/doc/<doc_id>/page/<int:page>/replace")
    def replace(doc_id, page):
        ed = get_doc(doc_id)
        body = request.get_json(force=True)
        bbox = body.get("bbox")
        if not bbox or len(bbox) != 4:
            abort(400, "bbox 가 필요합니다.")
        new_bbox = body.get("new_bbox")
        if new_bbox is not None and len(new_bbox) != 4:
            abort(400, "new_bbox 는 숫자 4개여야 합니다.")
        ed.replace_text(
            page,
            [float(v) for v in bbox],
            body.get("text", ""),
            float(body["font_size"]) if body.get("font_size") else None,
            body.get("color") or "#000000",
            target_bbox=[float(v) for v in new_bbox] if new_bbox else None,
        )
        return jsonify(doc_state(doc_id))

    @app.post("/api/doc/<doc_id>/page/<int:page>/add_text")
    def add_text(doc_id, page):
        ed = get_doc(doc_id)
        body = request.get_json(force=True)
        text = body.get("text", "")
        if not text.strip():
            abort(400, "추가할 텍스트를 입력하세요.")
        rect = ed.add_text(
            page,
            float(body.get("x", 72)),
            float(body.get("y", 72)),
            text,
            float(body.get("font_size") or 12),
            body.get("color") or "#000000",
            float(body["max_width"]) if body.get("max_width") else None,
            float(body["height"]) if body.get("height") else None,
        )
        return jsonify({**doc_state(doc_id), "rect": rect})

    @app.post("/api/doc/<doc_id>/page/<int:page>/ink")
    def ink(doc_id, page):
        """펜/형광펜 획 추가. points 는 화면 좌표(pt) [[x, y], ...]."""
        ed = get_doc(doc_id)
        body = request.get_json(force=True) or {}
        pts = body.get("points") or []
        if not isinstance(pts, list) or not pts:
            abort(400, "points 가 필요합니다.")
        try:
            pts = [(float(p[0]), float(p[1])) for p in pts]
        except (TypeError, ValueError, IndexError):
            abort(400, "points 형식이 잘못되었습니다.")
        ed.draw_stroke(
            page,
            pts,
            body.get("color") or "#000000",
            float(body.get("width") or 2),
            float(body.get("opacity") or 1),
            bool(body.get("smooth", True)),
        )
        return jsonify(doc_state(doc_id))

    @app.post("/api/doc/<doc_id>/find_replace")
    def find_replace(doc_id):
        ed = get_doc(doc_id)
        body = request.get_json(force=True)
        old = body.get("find", "")
        if not old:
            abort(400, "찾을 문자열을 입력하세요.")
        count = ed.find_and_replace(old, body.get("replace", ""))
        return jsonify({**doc_state(doc_id), "count": count})

    # ---------------------------------------------------------------- 페이지 조작
    @app.post("/api/doc/<doc_id>/reorder")
    def reorder(doc_id):
        ed = get_doc(doc_id)
        order = (request.get_json(force=True) or {}).get("order")
        if not isinstance(order, list):
            abort(400, "order 목록이 필요합니다.")
        ed.reorder_pages([int(i) for i in order])
        return jsonify(doc_state(doc_id))

    @app.post("/api/doc/<doc_id>/move")
    def move(doc_id):
        ed = get_doc(doc_id)
        body = request.get_json(force=True)
        ed.move_page(int(body["from"]), int(body["to"]))
        return jsonify(doc_state(doc_id))

    @app.post("/api/doc/<doc_id>/delete")
    def delete(doc_id):
        ed = get_doc(doc_id)
        body = request.get_json(force=True)
        ed.delete_pages([int(i) for i in body.get("pages", [])])
        return jsonify(doc_state(doc_id))

    @app.post("/api/doc/<doc_id>/rotate")
    def rotate(doc_id):
        ed = get_doc(doc_id)
        body = request.get_json(force=True)
        ed.rotate_page(int(body["page"]), int(body.get("degrees", 90)))
        return jsonify(doc_state(doc_id))

    @app.post("/api/doc/<doc_id>/duplicate")
    def duplicate(doc_id):
        ed = get_doc(doc_id)
        ed.duplicate_page(int(request.get_json(force=True)["page"]))
        return jsonify(doc_state(doc_id))

    @app.post("/api/doc/<doc_id>/blank")
    def blank(doc_id):
        ed = get_doc(doc_id)
        body = request.get_json(force=True) or {}
        after = body.get("after")
        ed.insert_blank_page(None if after is None else int(after) + 1)
        return jsonify(doc_state(doc_id))

    @app.post("/api/doc/<doc_id>/undo")
    def undo(doc_id):
        get_doc(doc_id).undo()
        return jsonify(doc_state(doc_id))

    @app.post("/api/doc/<doc_id>/redo")
    def redo(doc_id):
        get_doc(doc_id).redo()
        return jsonify(doc_state(doc_id))

    # ---------------------------------------------------------------- 페이지 추출
    @app.post("/api/doc/<doc_id>/extract")
    def extract(doc_id):
        ed = get_doc(doc_id)
        body = request.get_json(force=True) or {}
        if "pages" in body and isinstance(body["pages"], list):
            indices = [int(i) for i in body["pages"]]
        else:
            indices = parse_page_range(str(body.get("range", "")), ed.page_count)
        data = ed.extract_pages(indices)
        save_path = body.get("path")
        if save_path:
            target = Path(save_path).expanduser()
            target.write_bytes(data)
            return jsonify({"ok": True, "path": str(target), "count": len(indices)})
        name = names[doc_id]
        stem = name[:-4] if name.lower().endswith(".pdf") else name
        return send_file(
            io.BytesIO(data),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=f"{stem}_pages.pdf",
        )

    @app.post("/api/doc/<doc_id>/extract_each")
    def extract_each(doc_id):
        """선택한 페이지를 각각 별도 PDF 로 저장 (서버 로컬 폴더에)."""
        ed = get_doc(doc_id)
        body = request.get_json(force=True) or {}
        indices = parse_page_range(str(body.get("range", "")), ed.page_count)
        folder = Path(body.get("folder") or ".").expanduser()
        folder.mkdir(parents=True, exist_ok=True)
        name = names[doc_id]
        stem = name[:-4] if name.lower().endswith(".pdf") else name
        written = []
        for i in indices:
            target = folder / f"{stem}_p{i + 1}.pdf"
            target.write_bytes(ed.extract_pages([i]))
            written.append(str(target))
        return jsonify({"ok": True, "files": written})

    return app


def run(host: str = "127.0.0.1", port: int = 8765, initial_file: str | None = None, open_browser: bool = True, debug: bool = False):
    app = create_app(initial_file)
    url = f"http://{host}:{port}/"
    print(f"PDF 편집기 실행 중: {url}  (종료: Ctrl+C)")
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    app.run(host=host, port=port, debug=debug, use_reloader=False, threaded=True)

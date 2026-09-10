import io

import pymupdf
import pytest

from pdfeditor.app import create_app
from tests.test_core import make_pdf


@pytest.fixture
def client():
    app = create_app()
    app.testing = True
    return app.test_client()


def open_doc(client, n=3):
    r = client.post("/api/open", data={"file": (io.BytesIO(make_pdf(n)), "t.pdf")}, content_type="multipart/form-data")
    assert r.status_code == 200
    return r.get_json()


def test_open_and_state(client):
    st = open_doc(client)
    assert st["page_count"] == 3 and st["name"] == "t.pdf" and st["can_undo"] is False
    r = client.get(f"/api/doc/{st['id']}")
    assert r.get_json()["page_count"] == 3


def test_open_missing_file(client):
    r = client.post("/api/open", json={"path": "/no/such.pdf"})
    assert r.status_code == 400 and "찾을 수 없습니다" in r.get_json()["error"]


def test_page_image_and_blocks(client):
    d = open_doc(client)["id"]
    r = client.get(f"/api/doc/{d}/page/0/image?zoom=0.5")
    assert r.status_code == 200 and r.mimetype == "image/png"
    r = client.get(f"/api/doc/{d}/page/0/blocks")
    body = r.get_json()
    assert body["width"] == 595 and any(b["text"] == "Title 1" for b in body["blocks"])


def test_replace_add_and_undo(client):
    d = open_doc(client)["id"]
    blk = client.get(f"/api/doc/{d}/page/0/blocks").get_json()["blocks"][0]
    r = client.post(f"/api/doc/{d}/page/0/replace", json={"bbox": blk["bbox"], "text": "수정", "font_size": 18, "color": "#ff0000"})
    assert r.status_code == 200 and r.get_json()["can_undo"] is True
    assert "수정" in client.get(f"/api/doc/{d}/page/0/text").get_json()["text"]
    r = client.post(f"/api/doc/{d}/page/0/add_text", json={"x": 72, "y": 400, "text": "추가", "font_size": 12})
    assert r.status_code == 200 and r.get_json()["rect"][0] == 72
    r = client.post(f"/api/doc/{d}/page/0/add_text", json={"x": 72, "y": 400, "text": "  "})
    assert r.status_code == 400
    client.post(f"/api/doc/{d}/undo")
    assert "추가" not in client.get(f"/api/doc/{d}/page/0/text").get_json()["text"]


def test_add_text_box_and_replace_target(client):
    d = open_doc(client)["id"]
    r = client.post(f"/api/doc/{d}/page/0/add_text", json={"x": 72, "y": 500, "text": "상자", "max_width": 100, "height": 20})
    assert r.status_code == 200
    blk = client.get(f"/api/doc/{d}/page/0/blocks").get_json()["blocks"][0]
    r = client.post(f"/api/doc/{d}/page/0/replace", json={"bbox": blk["bbox"], "text": "이동", "new_bbox": [300, 600, 400, 620]})
    assert r.status_code == 200
    moved = next(b for b in client.get(f"/api/doc/{d}/page/0/blocks").get_json()["blocks"] if b["text"] == "이동")
    assert abs(moved["bbox"][0] - 300) < 3
    r = client.post(f"/api/doc/{d}/page/0/replace", json={"bbox": blk["bbox"], "text": "x", "new_bbox": [1, 2]})
    assert r.status_code == 400


def test_reorder_move_delete_rotate(client):
    d = open_doc(client)["id"]
    assert client.post(f"/api/doc/{d}/reorder", json={"order": [2, 1, 0]}).status_code == 200
    assert client.get(f"/api/doc/{d}/page/0/text").get_json()["text"].startswith("Title 3")
    assert client.post(f"/api/doc/{d}/reorder", json={"order": [0, 0, 1]}).status_code == 400
    assert client.post(f"/api/doc/{d}/move", json={"from": 0, "to": 2}).status_code == 200
    assert client.get(f"/api/doc/{d}/page/2/text").get_json()["text"].startswith("Title 3")
    st = client.post(f"/api/doc/{d}/rotate", json={"page": 0, "degrees": 90}).get_json()
    assert st["pages"][0]["rotation"] == 90
    st = client.post(f"/api/doc/{d}/delete", json={"pages": [0]}).get_json()
    assert st["page_count"] == 2
    assert client.post(f"/api/doc/{d}/delete", json={"pages": [0, 1]}).status_code == 400


def test_extract_download(client, tmp_path):
    d = open_doc(client, 4)["id"]
    r = client.post(f"/api/doc/{d}/extract", json={"range": "1-2, 4"})
    assert r.status_code == 200 and r.mimetype == "application/pdf"
    assert "t_pages.pdf" in r.headers["Content-Disposition"]
    assert pymupdf.open(stream=r.data, filetype="pdf").page_count == 3
    r = client.post(f"/api/doc/{d}/extract", json={"range": "7"})
    assert r.status_code == 400
    r = client.post(f"/api/doc/{d}/extract", json={"pages": [3], "path": str(tmp_path / "x.pdf")})
    assert r.get_json()["count"] == 1 and (tmp_path / "x.pdf").exists()
    r = client.post(f"/api/doc/{d}/extract_each", json={"range": "1,3", "folder": str(tmp_path / "each")})
    assert len(r.get_json()["files"]) == 2 and (tmp_path / "each" / "t_p3.pdf").exists()


def test_find_replace_download_save(client, tmp_path):
    d = open_doc(client)["id"]
    r = client.post(f"/api/doc/{d}/find_replace", json={"find": "Right", "replace": "오른쪽"})
    assert r.get_json()["count"] == 3
    r = client.get(f"/api/doc/{d}/download")
    assert r.status_code == 200 and "t_edited.pdf" in r.headers["Content-Disposition"]
    assert "오른쪽" in pymupdf.open(stream=r.data, filetype="pdf")[0].get_text()
    assert client.post(f"/api/doc/{d}/save", json={}).status_code == 400  # 업로드 문서는 경로 필요
    r = client.post(f"/api/doc/{d}/save", json={"path": str(tmp_path / "s.pdf")})
    assert r.get_json()["ok"] and (tmp_path / "s.pdf").exists()


def test_unknown_doc(client):
    assert client.get("/api/doc/nope").status_code == 404
    assert client.get("/").status_code == 200


def test_ink_and_image_cache_headers(client):
    d = open_doc(client)["id"]
    key = client.get(f"/api/doc/{d}").get_json()["pages"][0]["key"]
    r = client.get(f"/api/doc/{d}/page/0/image?zoom=0.5&v={key}")
    assert r.status_code == 200 and "immutable" in r.headers["Cache-Control"]
    r = client.get(f"/api/doc/{d}/page/0/image?zoom=0.5")
    assert r.headers["Cache-Control"] == "no-store"
    r = client.post(f"/api/doc/{d}/page/0/ink", json={"points": [[100, 100], [150, 120], [200, 100]], "color": "#ff0000", "width": 3})
    st = r.get_json()
    assert r.status_code == 200 and st["pages"][0]["key"] != key and st["pages"][1]["key"] == client.get(f"/api/doc/{d}").get_json()["pages"][1]["key"]
    assert client.post(f"/api/doc/{d}/page/0/ink", json={"points": []}).status_code == 400
    assert client.post(f"/api/doc/{d}/page/0/ink", json={"points": [["a", 1]]}).status_code == 400

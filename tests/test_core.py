import pymupdf
import pytest

from pdfeditor import PDFEditor, parse_page_range


def make_pdf(n_pages=4) -> bytes:
    doc = pymupdf.open()
    for i in range(n_pages):
        p = doc.new_page()
        p.insert_text((72, 100), f"Title {i + 1}", fontsize=18)
        p.insert_text((300, 100), "Right", fontsize=18)
        p.insert_textbox(pymupdf.Rect(72, 150, 400, 300), f"Body of page {i + 1}. " * 8, fontsize=11)
    return doc.tobytes()


def first_line(ed: PDFEditor, i: int) -> str:
    return ed.get_page_text(i).strip().split("\n")[0]


@pytest.fixture
def ed():
    editor = PDFEditor(make_pdf())
    yield editor
    editor.close()


# ---------------------------------------------------------------- 페이지 범위
@pytest.mark.parametrize(
    "spec,expected",
    [
        ("1", [0]),
        ("1-3", [0, 1, 2]),
        ("1-2, 4", [0, 1, 3]),
        ("3-", [2, 3]),
        ("-2", [0, 1]),
        ("4,1", [3, 0]),
        ("2,2", [1, 1]),
        ("", [0, 1, 2, 3]),
        ("all", [0, 1, 2, 3]),
    ],
)
def test_parse_page_range(spec, expected):
    assert parse_page_range(spec, 4) == expected


@pytest.mark.parametrize("spec", ["0", "5", "3-1", "abc", "1-9"])
def test_parse_page_range_invalid(spec):
    with pytest.raises(ValueError):
        parse_page_range(spec, 4)


# ---------------------------------------------------------------- 텍스트 읽기
def test_text_blocks_split_by_gap_and_merge_paragraph(ed):
    blocks = ed.get_text_blocks(0)
    texts = [b.text for b in blocks]
    assert "Title 1" in texts
    assert "Right" in texts
    body = next(b for b in blocks if b.text.startswith("Body of page 1"))
    assert len(body.lines) > 1  # 여러 줄이 하나의 문단으로 묶임
    assert body.font_size == 11.0
    assert body.color == "#000000"


# ---------------------------------------------------------------- 텍스트 편집
def test_replace_block_keeps_other_text(ed):
    ed.replace_block(0, 0, "제목 수정", color="#ff0000")
    text = ed.get_page_text(0)
    assert "제목 수정" in text
    assert "Title 1" not in text
    assert "Right" in text
    changed = next(b for b in ed.get_text_blocks(0) if b.text == "제목 수정")
    assert changed.color == "#ff0000"
    assert changed.font_size == 18.0


def test_replace_with_empty_deletes(ed):
    blk = ed.get_text_blocks(0)[0]
    ed.replace_text(0, blk.bbox, "")
    assert "Title 1" not in ed.get_page_text(0)


def test_replace_long_text_shrinks_font(ed):
    blk = ed.get_text_blocks(0)[0]
    long_text = "아주 긴 제목 " * 12
    ed.replace_text(0, blk.bbox, long_text, font_size=18)
    new = next(b for b in ed.get_text_blocks(0) if b.text.startswith("아주 긴 제목"))
    assert new.font_size <= 18.0


def test_add_text_korean(ed):
    rect = ed.add_text(1, 72, 500, "안녕하세요\n둘째 줄", font_size=14, color="#0000ff")
    assert rect[0] == 72 and rect[1] == 500
    text = ed.get_page_text(1)
    assert "안녕하세요" in text and "둘째 줄" in text
    blk = next(b for b in ed.get_text_blocks(1) if "안녕하세요" in b.text)
    assert blk.color == "#0000ff"
    assert blk.font_size == 14.0


def test_add_text_wraps_within_width(ed):
    ed.add_text(0, 72, 600, "word " * 40, font_size=12, max_width=150)
    blk = next(b for b in ed.get_text_blocks(0) if b.bbox[1] >= 599)
    assert blk.bbox[2] <= 72 + 150 + 1
    assert len(blk.lines) > 1


def test_add_text_with_box_height_keeps_font_and_grows(ed):
    # 상자가 너무 작아도 글자 크기는 유지하고 아래로 늘려서 넣는다
    ed.add_text(0, 72, 600, "첫 줄\n둘째 줄\n셋째 줄", font_size=14, max_width=200, height=10)
    blk = next(b for b in ed.get_text_blocks(0) if "첫 줄" in b.text)
    assert blk.font_size == 14.0 and len(blk.lines) == 3


def test_replace_text_into_target_box(ed):
    blk = ed.get_text_blocks(0)[0]  # "Title 1"
    ed.replace_text(0, blk.bbox, "옮긴 제목", font_size=18, target_bbox=(300, 400, 500, 430))
    new = next(b for b in ed.get_text_blocks(0) if b.text == "옮긴 제목")
    assert abs(new.bbox[0] - 300) < 3 and abs(new.bbox[1] - 400) < 5
    assert new.font_size == 18.0
    assert "Title 1" not in ed.get_page_text(0)


def test_add_text_rejects_empty(ed):
    with pytest.raises(ValueError):
        ed.add_text(0, 10, 10, "")


def test_find_and_replace_all_pages(ed):
    n = ed.find_and_replace("Right", "오른쪽")
    assert n == 4
    for i in range(4):
        assert "오른쪽" in ed.get_page_text(i)
        assert "Right" not in ed.get_page_text(i)


def test_edit_on_rotated_page(ed):
    ed.rotate_page(0, 90)
    w, h = ed.page_size(0)
    assert (w, h) == (842.0, 595.0)
    ed.add_text(0, 50, 50, "회전", font_size=20)
    blk = next(b for b in ed.get_text_blocks(0) if b.text == "회전")
    # 화면 좌표 기준으로 클릭 지점 근처에 놓여야 함
    assert abs(blk.bbox[0] - 50) < 5 and abs(blk.bbox[1] - 50) < 5


# ---------------------------------------------------------------- 페이지 조작
def test_reorder_pages(ed):
    ed.reorder_pages([3, 2, 1, 0])
    assert [first_line(ed, i) for i in range(4)] == ["Title 4", "Title 3", "Title 2", "Title 1"]


def test_reorder_rejects_bad_order(ed):
    with pytest.raises(ValueError):
        ed.reorder_pages([0, 0, 1, 2])
    with pytest.raises(ValueError):
        ed.reorder_pages([0, 1])


def test_move_page(ed):
    ed.move_page(3, 0)
    assert [first_line(ed, i) for i in range(4)] == ["Title 4", "Title 1", "Title 2", "Title 3"]
    ed.move_page(0, 3)
    assert [first_line(ed, i) for i in range(4)] == ["Title 1", "Title 2", "Title 3", "Title 4"]


def test_delete_pages(ed):
    ed.delete_pages([0, 2])
    assert ed.page_count == 2
    assert [first_line(ed, i) for i in range(2)] == ["Title 2", "Title 4"]


def test_cannot_delete_all(ed):
    with pytest.raises(ValueError):
        ed.delete_pages([0, 1, 2, 3])


def test_duplicate_and_blank(ed):
    ed.duplicate_page(0)
    assert ed.page_count == 5 and first_line(ed, 1) == "Title 1"
    ed.insert_blank_page(0)
    assert ed.page_count == 6 and ed.get_page_text(0).strip() == ""


def test_undo_redo(ed):
    assert not ed.can_undo
    ed.reorder_pages([1, 0, 2, 3])
    assert ed.can_undo and first_line(ed, 0) == "Title 2"
    assert ed.undo() and first_line(ed, 0) == "Title 1"
    assert ed.can_redo
    assert ed.redo() and first_line(ed, 0) == "Title 2"
    assert not ed.undo() or True  # 스택 비어도 예외 없이 False 반환
    assert ed.undo() is False


# ---------------------------------------------------------------- 추출 / 저장
def test_extract_pages_by_range(ed):
    data = ed.extract_pages("2-3")
    out = pymupdf.open(stream=data, filetype="pdf")
    assert out.page_count == 2
    assert out[0].get_text().startswith("Title 2")
    assert ed.page_count == 4  # 원본은 그대로


def test_extract_pages_by_list_and_repeat(ed):
    out = pymupdf.open(stream=ed.extract_pages([3, 0, 0]), filetype="pdf")
    assert [out[i].get_text().split("\n")[0] for i in range(3)] == ["Title 4", "Title 1", "Title 1"]


def test_extract_after_edit_is_small(ed):
    ed.add_text(0, 72, 500, "한글")
    assert len(ed.extract_pages([0])) < 200_000


def test_save_and_reopen(tmp_path):
    src = tmp_path / "in.pdf"
    src.write_bytes(make_pdf(2))
    ed = PDFEditor(src)
    ed.add_text(0, 72, 500, "저장 테스트")
    ed.reorder_pages([1, 0])
    out = ed.save(tmp_path / "out.pdf")
    assert out.exists() and out.stat().st_size < 200_000  # 폰트 서브셋 적용
    re = PDFEditor(out)
    assert first_line(re, 0) == "Title 2"
    assert "저장 테스트" in re.get_page_text(1)
    ed.save()  # 마지막 저장 경로에 덮어쓰기
    assert ed.path == out


def test_render_page_png(ed):
    png = ed.render_page(0, zoom=0.5)
    assert png[:4] == b"\x89PNG"


# ---------------------------------------------------------------- 페이지 키 / 펜
def test_page_keys_change_only_for_edited_pages(ed):
    keys = [p["key"] for p in ed.page_info()]
    ed.add_text(1, 72, 500, "키 테스트")
    after = [p["key"] for p in ed.page_info()]
    assert after[1] != keys[1]
    assert after[0] == keys[0] and after[2] == keys[2] and after[3] == keys[3]
    ed.reorder_pages([3, 2, 1, 0])
    assert [p["key"] for p in ed.page_info()] == list(reversed(after))
    ed.delete_pages([0])
    assert [p["key"] for p in ed.page_info()] == list(reversed(after))[1:]
    ed.undo()  # 삭제 취소 → 키도 복원
    assert [p["key"] for p in ed.page_info()] == list(reversed(after))


def test_draw_stroke_smooth_and_highlighter(ed):
    pts = [(100, 500), (140, 520), (180, 500), (220, 540), (260, 500)]
    ed.draw_stroke(0, pts, color="#ff0000", width=3)
    ed.draw_stroke(0, [(100, 600), (300, 600)], color="#ffeb3b", width=14, opacity=0.35)
    ed.draw_stroke(0, [(50, 50)], color="#000000", width=4)  # 점 하나 → 원
    drawings = ed._page(0).get_drawings()
    assert len(drawings) >= 3
    red = next(d for d in drawings if d.get("color") and abs(d["color"][0] - 1) < 0.01 and d["color"][1] < 0.01)
    assert any(item[0] == "c" for item in red["items"])  # 베지어 곡선으로 그려짐
    hl = next(d for d in drawings if d.get("stroke_opacity") is not None and d["stroke_opacity"] < 0.5)
    assert hl["width"] == 14
    assert ed.can_undo
    with pytest.raises(ValueError):
        ed.draw_stroke(0, [])


def test_unreadable_glyphs_are_flagged_and_stripped(ed, monkeypatch):
    """폰트에 유니코드 매핑이 없어 U+FFFD 로 나오는 글자는 text 에서 빼고 unreadable 로 표시한다."""
    page = ed._page(0)
    real = page.get_text("dict")
    fake_line = {
        "bbox": (100, 400, 160, 414),
        "spans": [{"text": "���", "bbox": (100, 400, 160, 414), "size": 11.0, "color": 0}],
    }
    mixed_line = {
        "bbox": (100, 500, 200, 514),
        "spans": [{"text": "지원자 \ufffd\x00", "bbox": (100, 500, 200, 514), "size": 11.0, "color": 0}],
    }
    real["blocks"].append({"type": 0, "bbox": (100, 400, 160, 414), "lines": [fake_line]})
    real["blocks"].append({"type": 0, "bbox": (100, 500, 200, 514), "lines": [mixed_line]})
    monkeypatch.setattr(type(page), "get_text", lambda self, *a, **k: real)
    blocks = ed.get_text_blocks(0)
    bad = next(b for b in blocks if b.bbox[1] == 400)
    assert bad.unreadable and bad.text == ""
    mixed = next(b for b in blocks if b.bbox[1] == 500)
    assert mixed.unreadable and mixed.text == "지원자"
    assert all(not b.unreadable for b in blocks if b.bbox[1] not in (400, 500))
    assert "unreadable" in bad.to_dict()


def test_text_added_after_save_and_after_reopen_stays_visible():
    """저장(폰트 서브셋) 뒤에도, 저장한 파일을 다시 연 뒤에도 새로 넣는 글자가 정상이어야 한다."""
    ed = PDFEditor()
    ed.add_text(0, 72, 100, "안녕", font_size=14)
    data = ed.to_bytes()
    ed.add_text(0, 72, 150, "후보자", font_size=14)
    assert "후보자" in ed.get_page_text(0)
    assert [b.text for b in ed.get_text_blocks(0)] == ["안녕", "후보자"]
    assert not any(b.unreadable for b in ed.get_text_blocks(0))
    re = PDFEditor(data)
    assert "안녕" in re.get_page_text(0)
    re.add_text(0, 72, 150, "후보자", font_size=14)
    assert "후보자" in re.get_page_text(0)
    assert not any(b.unreadable for b in re.get_text_blocks(0))
    # 두 번 저장해도 크기가 작게 유지됨 (서브셋이 사본에 적용)
    assert len(re.to_bytes()) < 400_000

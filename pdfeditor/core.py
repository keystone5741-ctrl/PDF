"""PDF 편집 핵심 로직 (PyMuPDF 기반).

GUI/CLI 와 독립적으로 사용할 수 있습니다::

    from pdfeditor import PDFEditor
    ed = PDFEditor("input.pdf")
    ed.add_text(0, 72, 72, "안녕하세요", font_size=14)
    ed.reorder_pages([2, 0, 1])
    ed.save("output.pdf")

좌표계는 항상 "화면에 보이는 대로" (페이지 회전이 적용된 상태) 의 PDF 포인트 단위입니다.
"""

from __future__ import annotations

import io
import re
import threading
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

import pymupdf

# 한글을 포함한 CJK 문자를 표시하기 위한 내장 폴백 폰트 (Droid Sans Fallback)
FONT_NAME = "PDFEditorCJK"
_FALLBACK_FONT: pymupdf.Font | None = None

MAX_UNDO = 15  # 실행 취소 사본 개수 (문서 전체 사본이라 너무 크게 잡으면 메모리를 많이 씀)
LINE_HEIGHT = 1.2  # 삽입하는 텍스트의 줄 간격 (글자 크기 배수). 화면 편집 상자와 동일하게 유지.


def _fallback_font() -> pymupdf.Font:
    global _FALLBACK_FONT
    if _FALLBACK_FONT is None:
        _FALLBACK_FONT = pymupdf.Font("cjk")
    return _FALLBACK_FONT


def _int_to_rgb(color: int) -> tuple[float, float, float]:
    return ((color >> 16) & 255) / 255, ((color >> 8) & 255) / 255, (color & 255) / 255


def _normalize_color(color) -> tuple[float, float, float]:
    """'#rrggbb' 문자열, 0~255 정수 튜플, 0~1 실수 튜플을 모두 (r, g, b) 실수로 변환."""
    if color is None:
        return (0.0, 0.0, 0.0)
    if isinstance(color, str):
        c = color.lstrip("#")
        if len(c) == 3:
            c = "".join(ch * 2 for ch in c)
        if len(c) != 6:
            raise ValueError(f"잘못된 색상 값: {color!r}")
        return tuple(int(c[i : i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]
    r, g, b = color
    if max(r, g, b) > 1:
        return (r / 255, g / 255, b / 255)
    return (float(r), float(g), float(b))


def parse_page_range(spec: str, page_count: int) -> list[int]:
    """'1-3,5,8-' 형태의 문자열을 0 기반 페이지 번호 목록으로 변환합니다.

    - 페이지 번호는 1부터 시작합니다.
    - '3-' 는 3페이지부터 끝까지, '-3' 은 처음부터 3페이지까지입니다.
    - 순서가 유지되며 중복은 허용됩니다 (같은 페이지를 두 번 추출 가능).
    """
    if page_count <= 0:
        raise ValueError("페이지가 없는 문서입니다.")
    spec = spec.strip()
    if not spec or spec.lower() in ("all", "전체", "*"):
        return list(range(page_count))

    result: list[int] = []
    for raw in re.split(r"[,\s]+", spec):
        part = raw.strip()
        if not part:
            continue
        m = re.fullmatch(r"(\d*)\s*-\s*(\d*)", part)
        if m:
            start = int(m.group(1)) if m.group(1) else 1
            end = int(m.group(2)) if m.group(2) else page_count
            if start < 1 or end > page_count or start > end:
                raise ValueError(f"잘못된 페이지 범위: {part} (1~{page_count} 사이여야 합니다)")
            result.extend(range(start - 1, end))
        elif part.isdigit():
            n = int(part)
            if n < 1 or n > page_count:
                raise ValueError(f"잘못된 페이지 번호: {n} (1~{page_count} 사이여야 합니다)")
            result.append(n - 1)
        else:
            raise ValueError(f"페이지 범위를 이해할 수 없습니다: {part!r}")
    if not result:
        raise ValueError("선택된 페이지가 없습니다.")
    return result


@dataclass
class TextBlock:
    """페이지 안의 편집 가능한 텍스트 덩어리 (화면 좌표 기준)."""

    id: int
    bbox: tuple[float, float, float, float]
    text: str
    font_size: float
    color: str  # '#rrggbb'
    lines: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


class PDFEditor:
    """PDF 문서 하나를 편집하는 객체."""

    def __init__(self, source: str | Path | bytes | None = None):
        self.path: Path | None = None
        if source is None:
            self.doc = pymupdf.open()
            self.doc.new_page()
        elif isinstance(source, (bytes, bytearray)):
            self.doc = pymupdf.open(stream=bytes(source), filetype="pdf")
        else:
            self.path = Path(source)
            self.doc = pymupdf.open(str(self.path))
        if self.doc.is_encrypted and not self.doc.authenticate(""):
            raise ValueError("암호로 보호된 PDF 입니다. 먼저 암호를 해제하세요.")
        self._undo: list[tuple[bytes, list[str]]] = []
        self._redo: list[tuple[bytes, list[str]]] = []
        # 페이지마다 내용이 바뀔 때만 달라지는 키. 화면에서 썸네일/이미지 캐시 무효화에 씁니다.
        self._page_keys: list[str] = [self._new_key() for _ in range(self.doc.page_count)]
        # PyMuPDF 는 스레드 안전하지 않으므로 문서 단위로 모든 작업을 직렬화합니다.
        self.lock = threading.RLock()

    # ------------------------------------------------------------------ 페이지 키
    @staticmethod
    def _new_key() -> str:
        return uuid.uuid4().hex[:10]

    def _touch(self, index: int) -> None:
        """index 페이지의 내용이 바뀌었음을 표시."""
        self._page_keys[index] = self._new_key()

    def page_key(self, index: int) -> str:
        self._page(index)
        return self._page_keys[index]

    # ------------------------------------------------------------------ 기본 정보
    def close(self) -> None:
        self.doc.close()

    @property
    def page_count(self) -> int:
        return self.doc.page_count

    def _page(self, index: int) -> pymupdf.Page:
        if not 0 <= index < self.page_count:
            raise IndexError(f"페이지 번호 범위 초과: {index + 1} (총 {self.page_count}페이지)")
        return self.doc[index]

    def page_size(self, index: int) -> tuple[float, float]:
        """회전이 적용된 (너비, 높이) 포인트."""
        rect = self._page(index).rect
        return rect.width, rect.height

    def page_info(self) -> list[dict]:
        out = []
        for i, page in enumerate(self.doc):
            out.append(
                {
                    "index": i,
                    "number": i + 1,
                    "width": page.rect.width,
                    "height": page.rect.height,
                    "rotation": page.rotation,
                    "key": self._page_keys[i],
                }
            )
        return out

    # ------------------------------------------------------------------ 렌더링
    def render_page(self, index: int, zoom: float = 1.5, fmt: str = "png") -> bytes:
        page = self._page(index)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        return pix.tobytes(fmt)

    # ------------------------------------------------------------------ 텍스트 읽기
    def get_text_blocks(self, index: int) -> list[TextBlock]:
        """페이지의 편집 가능한 텍스트 블록(문단) 목록.

        PyMuPDF 는 같은 줄에 있는 텍스트를 멀리 떨어져 있어도 한 블록으로 묶으므로,
        큰 가로 간격은 잘라내고, 세로로 붙어 있는 줄들만 하나의 문단으로 다시 묶습니다.
        """
        page = self._page(index)
        rot = page.rotation_matrix
        data = page.get_text("dict", flags=pymupdf.TEXT_PRESERVE_WHITESPACE)

        # 1) 줄을 조각(segment)으로 분할: (block_no, rect, text, size, color)
        segments: list[tuple[int, pymupdf.Rect, str, float, int]] = []
        for bno, block in enumerate(data["blocks"]):
            if block.get("type") != 0:
                continue
            for line in block["lines"]:
                cur_spans: list[dict] = []
                prev_x1 = None
                for span in line["spans"]:
                    sx0, _, sx1, _ = span["bbox"]
                    gap_limit = max(span["size"], 6) * 1.5
                    if cur_spans and prev_x1 is not None and sx0 - prev_x1 > gap_limit:
                        segments.append(_segment_from_spans(bno, cur_spans))
                        cur_spans = []
                    cur_spans.append(span)
                    prev_x1 = max(sx1, prev_x1 or sx1)
                if cur_spans:
                    segments.append(_segment_from_spans(bno, cur_spans))
        segments = [sg for sg in segments if sg[2].strip()]

        # 2) 세로로 인접하고 가로로 겹치는 조각들을 문단으로 병합
        paragraphs: list[dict] = []
        for bno, rect, text, size, color in segments:
            merged = False
            for para in paragraphs:
                if para["bno"] != bno:
                    continue
                pr: pymupdf.Rect = para["rect"]
                v_gap = rect.y0 - pr.y1
                h_overlap = min(rect.x1, pr.x1) - max(rect.x0, pr.x0)
                if -size * 0.3 <= v_gap <= max(size, para["size"]) * 0.9 and h_overlap > 0:
                    para["rect"] |= rect
                    para["lines"].append(text.rstrip())
                    merged = True
                    break
            if not merged:
                paragraphs.append({"bno": bno, "rect": pymupdf.Rect(rect), "lines": [text.rstrip()], "size": size, "color": color})

        blocks: list[TextBlock] = []
        paragraphs.sort(key=lambda pr: (round((pr["rect"] * rot).y0), round((pr["rect"] * rot).x0)))
        for para in paragraphs:
            rect = para["rect"] * rot
            rect.normalize()
            r, g, b = _int_to_rgb(para["color"])
            blocks.append(
                TextBlock(
                    id=len(blocks),
                    bbox=(rect.x0, rect.y0, rect.x1, rect.y1),
                    text="\n".join(para["lines"]),
                    font_size=round(para["size"], 2) or 11.0,
                    color="#%02x%02x%02x" % (int(r * 255), int(g * 255), int(b * 255)),
                    lines=para["lines"],
                )
            )
        return blocks

    def get_page_text(self, index: int) -> str:
        return self._page(index).get_text()

    def search_text(self, needle: str) -> list[dict]:
        """모든 페이지에서 문자열 검색. 결과는 화면 좌표의 bbox 목록."""
        hits = []
        for i, page in enumerate(self.doc):
            rot = page.rotation_matrix
            for r in page.search_for(needle):
                rr = r * rot
                hits.append({"page": i, "bbox": (rr.x0, rr.y0, rr.x1, rr.y1)})
        return hits

    # ------------------------------------------------------------------ 실행 취소
    def _state(self) -> tuple[bytes, list[str]]:
        return self.doc.tobytes(deflate=True), list(self._page_keys)

    def _snapshot(self) -> None:
        self._undo.append(self._state())
        if len(self._undo) > MAX_UNDO:
            self._undo.pop(0)
        self._redo.clear()

    def _restore(self, state: tuple[bytes, list[str]]) -> None:
        data, keys = state
        self.doc.close()
        self.doc = pymupdf.open(stream=data, filetype="pdf")
        self._page_keys = list(keys)

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self) -> bool:
        if not self._undo:
            return False
        self._redo.append(self._state())
        self._restore(self._undo.pop())
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self._undo.append(self._state())
        self._restore(self._redo.pop())
        return True

    # ------------------------------------------------------------------ 텍스트 쓰기
    def _ensure_font(self, page: pymupdf.Page) -> str:
        page.insert_font(fontname=FONT_NAME, fontbuffer=_fallback_font().buffer)
        return FONT_NAME

    def add_text(
        self,
        index: int,
        x: float,
        y: float,
        text: str,
        font_size: float = 12.0,
        color="#000000",
        max_width: float | None = None,
        height: float | None = None,
    ) -> tuple[float, float, float, float]:
        """(x, y) 를 왼쪽 위 모서리로 하여 텍스트를 추가합니다.

        max_width 를 주면 그 너비 안에서 자동 줄바꿈됩니다. height 를 주면 그 높이의 상자 안에 넣고,
        넘치면 아래쪽으로 늘립니다. 반환값은 실제로 쓰인 영역(화면 좌표).
        """
        if not text:
            raise ValueError("추가할 텍스트가 비어 있습니다.")
        page = self._page(index)
        self._snapshot()
        fontname = self._ensure_font(page)
        rgb = _normalize_color(color)
        font = _fallback_font()
        line_h = font_size * LINE_HEIGHT
        lines = text.split("\n")
        width = max_width if max_width else max(font.text_length(ln, fontsize=font_size) for ln in lines) + 2
        page_w, page_h = page.rect.width, page.rect.height
        width = min(width, max(page_w - x, font_size))
        if height is None:
            if max_width:
                n_lines = sum(max(1, _wrap_count(font, ln, font_size, width)) for ln in lines)
            else:
                n_lines = len(lines)
            height = n_lines * line_h + font_size * 0.4
        rect = pymupdf.Rect(x, y, x + width, min(page_h, y + height))
        self._insert_in_box(page, rect, text, font_size, fontname, rgb, shrink=False)
        self._touch(index)
        return (rect.x0, rect.y0, rect.x1, rect.y1)

    def _insert_in_box(
        self,
        page: pymupdf.Page,
        box: pymupdf.Rect,
        text: str,
        size: float,
        fontname: str,
        rgb: tuple[float, float, float],
        shrink: bool,
    ) -> None:
        """box(화면 좌표) 안에 텍스트를 넣습니다.

        shrink=True 면 넘칠 때 글자 크기를 조금씩 줄이고, False 면 크기를 유지한 채 상자를
        아래로 늘립니다. 그래도 안 들어가면 페이지 끝까지 늘려서 강제로 넣습니다.
        """
        page_w, page_h = page.rect.width, page.rect.height

        def try_insert(rect: pymupdf.Rect, fsize: float) -> bool:
            r = rect * page.derotation_matrix
            r.normalize()
            return page.insert_textbox(r, text, fontsize=fsize, fontname=fontname, color=rgb, rotate=page.rotation, lineheight=LINE_HEIGHT) >= 0

        if try_insert(box, size):
            return
        if shrink:
            s = size
            while s > 4:
                s -= 0.5
                if try_insert(box, s):
                    return
            size = max(s, 4)
        # 아래로 늘려서 재시도
        tall = pymupdf.Rect(box.x0, box.y0, box.x1, page_h)
        if try_insert(tall, size):
            return
        # 오른쪽/아래 모두 페이지 끝까지
        big = pymupdf.Rect(box.x0, box.y0, page_w, page_h)
        if not try_insert(big, size):
            r = big * page.derotation_matrix
            r.normalize()
            page.insert_textbox(r, text, fontsize=4, fontname=fontname, color=rgb, rotate=page.rotation, lineheight=LINE_HEIGHT)

    def replace_text(
        self,
        index: int,
        bbox: Sequence[float],
        new_text: str,
        font_size: float | None = None,
        color="#000000",
        keep_images: bool = True,
        target_bbox: Sequence[float] | None = None,
    ) -> None:
        """bbox(화면 좌표) 안의 기존 텍스트를 지우고 new_text 로 바꿉니다.

        new_text 가 빈 문자열이면 텍스트를 삭제만 합니다.
        target_bbox 를 주면 새 텍스트를 그 상자에 넣고(넘치면 아래로 늘림), 주지 않으면 원래 자리에
        넣되 글자가 넘치면 폰트를 조금씩 줄여서 맞춥니다.
        """
        page = self._page(index)
        self._snapshot()
        self._touch(index)
        rect = pymupdf.Rect(*bbox)
        rect_unrot = rect * page.derotation_matrix
        rect_unrot.normalize()
        # 원본 텍스트 제거 (레닥션). 이미지는 유지.
        page.add_redact_annot(rect_unrot, fill=False)
        page.apply_redactions(
            images=pymupdf.PDF_REDACT_IMAGE_NONE if keep_images else pymupdf.PDF_REDACT_IMAGE_REMOVE,
            graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
        )
        if not new_text.strip():
            return
        fontname = self._ensure_font(page)
        rgb = _normalize_color(color)
        size = float(font_size or 11.0)
        if target_bbox is not None:
            box = pymupdf.Rect(*target_bbox)
            box.normalize()
            self._insert_in_box(page, box, new_text, size, fontname, rgb, shrink=False)
            return
        # 새 텍스트가 들어갈 영역: 원래 영역을 오른쪽 빈 공간까지 넓히고 아래로 약간 여유를 둠
        right = self._free_right_edge(index, rect)
        box = pymupdf.Rect(rect.x0, rect.y0, max(right, rect.x0 + size), rect.y1 + size * 0.5)
        self._insert_in_box(page, box, new_text, size, fontname, rgb, shrink=True)

    def _free_right_edge(self, index: int, rect: pymupdf.Rect, margin: float = 18.0) -> float:
        """rect 와 같은 높이에 있는 다른 텍스트 블록 직전까지, 없으면 페이지 오른쪽 여백까지의 x 좌표."""
        page = self._page(index)
        limit = max(rect.x1, page.rect.width - margin)
        for blk in self.get_text_blocks(index):
            bx0, by0, bx1, by1 = blk.bbox
            overlaps_vertically = by0 < rect.y1 and by1 > rect.y0
            if overlaps_vertically and bx0 >= rect.x1 - 1 and bx0 < limit:
                limit = max(rect.x1, bx0 - 4)
        return limit

    def replace_block(self, index: int, block_id: int, new_text: str, font_size=None, color=None) -> None:
        """get_text_blocks() 로 얻은 블록 id 로 텍스트를 교체합니다."""
        blocks = self.get_text_blocks(index)
        if not 0 <= block_id < len(blocks):
            raise IndexError(f"텍스트 블록 번호 범위 초과: {block_id}")
        blk = blocks[block_id]
        self.replace_text(index, blk.bbox, new_text, font_size or blk.font_size, color or blk.color)

    def find_and_replace(self, old: str, new: str, font_size: float | None = None, color=None) -> int:
        """모든 페이지에서 old 문자열이 들어 있는 줄을 찾아 통째로 교체합니다. 교체한 줄 수를 반환."""
        count = 0
        for i in range(self.page_count):
            page = self._page(i)
            rot = page.rotation_matrix
            data = page.get_text("dict")
            targets = []
            for block in data["blocks"]:
                if block.get("type") != 0:
                    continue
                for line in block["lines"]:
                    text = "".join(s["text"] for s in line["spans"])
                    if old in text:
                        span = next((s for s in line["spans"] if s["text"].strip()), line["spans"][0])
                        targets.append((pymupdf.Rect(line["bbox"]) * rot, text.replace(old, new), span))
            for rect, text, span in targets:
                r, g, b = _int_to_rgb(int(span["color"]))
                self.replace_text(
                    i,
                    (rect.x0, rect.y0, rect.x1, rect.y1),
                    text,
                    font_size or float(span["size"]),
                    color or (r, g, b),
                )
                count += 1
        return count

    # ------------------------------------------------------------------ 펜 그리기
    def draw_stroke(
        self,
        index: int,
        points: Sequence[Sequence[float]],
        color="#000000",
        width: float = 2.0,
        opacity: float = 1.0,
        smooth: bool = True,
    ) -> None:
        """화면 좌표 점 목록을 따라 선을 그립니다 (펜/형광펜).

        smooth=True 면 점들을 지나는 부드러운 곡선(Catmull-Rom → Bezier)으로 그립니다.
        """
        pts = [pymupdf.Point(float(x), float(y)) for x, y in points]
        if not pts:
            raise ValueError("그릴 점이 없습니다.")
        page = self._page(index)
        self._snapshot()
        self._touch(index)
        m = page.derotation_matrix
        pts = [p * m for p in pts]
        rgb = _normalize_color(color)
        width = max(0.2, float(width))
        opacity = min(1.0, max(0.05, float(opacity)))
        shape = page.new_shape()
        if len(pts) == 1:
            shape.draw_circle(pts[0], width / 2)
            shape.finish(color=rgb, fill=rgb, width=0, fill_opacity=opacity, stroke_opacity=opacity)
        elif len(pts) < 3 or not smooth:
            shape.draw_polyline(pts)
            shape.finish(color=rgb, width=width, lineCap=1, lineJoin=1, stroke_opacity=opacity)
        else:
            # Catmull-Rom 스플라인을 3차 베지어로 변환
            ext = [pts[0]] + list(pts) + [pts[-1]]
            for i in range(1, len(ext) - 2):
                p0, p1, p2, p3 = ext[i - 1], ext[i], ext[i + 1], ext[i + 2]
                c1 = p1 + (p2 - p0) * (1 / 6)
                c2 = p2 - (p3 - p1) * (1 / 6)
                shape.draw_bezier(p1, c1, c2, p2)
            shape.finish(color=rgb, width=width, lineCap=1, lineJoin=1, stroke_opacity=opacity)
        shape.commit()

    # ------------------------------------------------------------------ 페이지 조작
    def reorder_pages(self, order: Iterable[int]) -> None:
        """order 는 새 순서대로 나열한 0 기반 페이지 번호 목록 (모든 페이지를 정확히 한 번씩)."""
        order = list(order)
        if sorted(order) != list(range(self.page_count)):
            raise ValueError("새 순서에는 모든 페이지가 정확히 한 번씩 들어 있어야 합니다.")
        if order == list(range(self.page_count)):
            return
        self._snapshot()
        self.doc.select(order)
        self._page_keys = [self._page_keys[i] for i in order]

    def move_page(self, src: int, dst: int) -> None:
        """src 페이지를 dst 위치로 옮깁니다 (둘 다 0 기반)."""
        self._page(src)
        if not 0 <= dst < self.page_count:
            raise IndexError(f"이동할 위치 범위 초과: {dst + 1}")
        if src == dst:
            return
        order = list(range(self.page_count))
        order.insert(dst, order.pop(src))
        self.reorder_pages(order)

    def delete_pages(self, indices: Iterable[int]) -> None:
        indices = sorted(set(indices))
        for i in indices:
            self._page(i)
        if len(indices) >= self.page_count:
            raise ValueError("모든 페이지를 삭제할 수는 없습니다.")
        if not indices:
            return
        self._snapshot()
        self.doc.delete_pages(indices)
        drop = set(indices)
        self._page_keys = [k for i, k in enumerate(self._page_keys) if i not in drop]

    def rotate_page(self, index: int, degrees: int) -> None:
        page = self._page(index)
        self._snapshot()
        page.set_rotation((page.rotation + degrees) % 360)
        self._touch(index)

    def insert_blank_page(self, index: int | None = None, width: float = 595, height: float = 842) -> None:
        self._snapshot()
        self.doc.new_page(pno=-1 if index is None else index, width=width, height=height)
        self._page_keys.insert(self.page_count - 1 if index is None else index, self._new_key())

    def duplicate_page(self, index: int) -> None:
        self._page(index)
        self._snapshot()
        self.doc.fullcopy_page(index, index + 1)
        self._page_keys.insert(index + 1, self._new_key())

    # ------------------------------------------------------------------ 추출 / 저장
    def extract_pages(self, pages: Iterable[int] | str) -> bytes:
        """지정한 페이지만 담은 새 PDF 의 바이트를 돌려줍니다 (원본은 바뀌지 않음)."""
        if isinstance(pages, str):
            indices = parse_page_range(pages, self.page_count)
        else:
            indices = list(pages)
            for i in indices:
                self._page(i)
        out = pymupdf.open()
        for i in indices:
            out.insert_pdf(self.doc, from_page=i, to_page=i)
        try:
            out.subset_fonts()
        except Exception:  # pragma: no cover
            pass
        data = out.tobytes(garbage=3, deflate=True)
        out.close()
        return data

    def extract_pages_to(self, pages: Iterable[int] | str, path: str | Path) -> Path:
        path = Path(path)
        path.write_bytes(self.extract_pages(pages))
        return path

    def to_bytes(self) -> bytes:
        self._subset_fonts()
        return self.doc.tobytes(garbage=3, deflate=True)

    def save(self, path: str | Path | None = None) -> Path:
        """path 를 생략하면 원본 파일 위치에 덮어씁니다."""
        target = Path(path) if path else self.path
        if target is None:
            raise ValueError("저장할 경로가 필요합니다.")
        target.write_bytes(self.to_bytes())
        self.path = target
        return target

    def _subset_fonts(self) -> None:
        """추가한 폴백 폰트(3MB+)를 실제로 쓰인 글자만 남기고 줄입니다."""
        try:
            self.doc.subset_fonts()
        except Exception:  # pragma: no cover - 서브셋 실패는 치명적이지 않음
            pass

    # ------------------------------------------------------------------ 기타
    def __enter__(self) -> "PDFEditor":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def _segment_from_spans(bno: int, spans: list[dict]) -> tuple[int, pymupdf.Rect, str, float, int]:
    rect = pymupdf.Rect(spans[0]["bbox"])
    for sp in spans[1:]:
        rect |= pymupdf.Rect(sp["bbox"])
    text = "".join(sp["text"] for sp in spans)
    first = next((sp for sp in spans if sp["text"].strip()), spans[0])
    return bno, rect, text, float(first["size"]), int(first["color"])


def _wrap_count(font: pymupdf.Font, line: str, size: float, width: float) -> int:
    """단순 단어 단위 줄바꿈 시 필요한 줄 수를 추정."""
    if not line:
        return 1
    n = 1
    cur = 0.0
    for word in re.split(r"(\s+)", line):
        w = font.text_length(word, fontsize=size)
        if cur + w > width and cur > 0:
            n += 1
            cur = w
        else:
            cur += w
    return n

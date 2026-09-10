"""명령줄 인터페이스.

    python main.py                          # 브라우저 편집기 실행
    python main.py 문서.pdf                 # 문서를 열면서 편집기 실행
    python main.py extract 문서.pdf "1-3,5" -o 추출.pdf
    python main.py reorder 문서.pdf "3,1,2" -o 순서변경.pdf
    python main.py delete 문서.pdf "2,4" -o 삭제.pdf
    python main.py add-text 문서.pdf 1 --x 72 --y 72 --text "안녕하세요" -o 결과.pdf
    python main.py replace 문서.pdf --find "기존 문구" --replace "새 문구" -o 결과.pdf
    python main.py blocks 문서.pdf 1        # 1페이지의 텍스트 블록 목록 보기
    python main.py edit-block 문서.pdf 1 0 --text "새 내용" -o 결과.pdf
    python main.py info 문서.pdf
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .core import PDFEditor, parse_page_range


def _out_path(args, editor: PDFEditor, suffix: str) -> Path:
    if args.output:
        return Path(args.output)
    src = editor.path or Path("output.pdf")
    return src.with_name(f"{src.stem}_{suffix}.pdf")


def cmd_serve(args):
    from .app import run

    run(host=args.host, port=args.port, initial_file=args.file, open_browser=not args.no_browser, debug=args.debug)


def cmd_info(args):
    ed = PDFEditor(args.file)
    print(f"파일: {ed.path}")
    print(f"페이지 수: {ed.page_count}")
    for p in ed.page_info():
        print(f"  {p['number']:>3}페이지  {p['width']:.0f} x {p['height']:.0f} pt  회전 {p['rotation']}°")


def cmd_extract(args):
    ed = PDFEditor(args.file)
    indices = parse_page_range(args.pages, ed.page_count)
    if args.each:
        folder = Path(args.output or ed.path.parent)
        folder.mkdir(parents=True, exist_ok=True)
        for i in indices:
            target = folder / f"{ed.path.stem}_p{i + 1}.pdf"
            ed.extract_pages_to([i], target)
            print(f"저장: {target}")
    else:
        target = _out_path(args, ed, "extracted")
        ed.extract_pages_to(indices, target)
        print(f"{len(indices)}페이지 추출 → {target}")


def cmd_reorder(args):
    ed = PDFEditor(args.file)
    order = parse_page_range(args.order, ed.page_count)
    if len(order) != ed.page_count:
        # 지정하지 않은 페이지는 원래 순서대로 뒤에 붙임
        missing = [i for i in range(ed.page_count) if i not in order]
        order = order + missing
    ed.reorder_pages(order)
    target = _out_path(args, ed, "reordered")
    ed.save(target)
    print(f"새 순서 {[i + 1 for i in order]} → {target}")


def cmd_delete(args):
    ed = PDFEditor(args.file)
    ed.delete_pages(parse_page_range(args.pages, ed.page_count))
    target = _out_path(args, ed, "deleted")
    ed.save(target)
    print(f"남은 페이지 {ed.page_count} → {target}")


def cmd_rotate(args):
    ed = PDFEditor(args.file)
    for i in parse_page_range(args.pages, ed.page_count):
        ed.rotate_page(i, args.degrees)
    target = _out_path(args, ed, "rotated")
    ed.save(target)
    print(f"회전 완료 → {target}")


def cmd_add_text(args):
    ed = PDFEditor(args.file)
    text = args.text if args.text is not None else sys.stdin.read()
    ed.add_text(args.page - 1, args.x, args.y, text, args.size, args.color, args.width)
    target = _out_path(args, ed, "edited")
    ed.save(target)
    print(f"텍스트 추가 → {target}")


def cmd_replace(args):
    ed = PDFEditor(args.file)
    n = ed.find_and_replace(args.find, args.replace, args.size, args.color)
    target = _out_path(args, ed, "edited")
    ed.save(target)
    print(f"{n}곳 교체 → {target}")


def cmd_blocks(args):
    ed = PDFEditor(args.file)
    for b in ed.get_text_blocks(args.page - 1):
        x0, y0, x1, y1 = b.bbox
        preview = b.text.replace("\n", " ⏎ ")
        print(f"[{b.id}] ({x0:.0f},{y0:.0f})-({x1:.0f},{y1:.0f}) {b.font_size}pt {b.color}: {preview}")


def cmd_edit_block(args):
    ed = PDFEditor(args.file)
    text = args.text if args.text is not None else sys.stdin.read()
    ed.replace_block(args.page - 1, args.block, text, args.size, args.color)
    target = _out_path(args, ed, "edited")
    ed.save(target)
    print(f"블록 {args.block} 수정 → {target}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pdfeditor", description="PDF 텍스트 수정/추가, 페이지 순서 변경, 페이지 추출 도구")
    sub = parser.add_subparsers(dest="command")

    s = sub.add_parser("serve", help="브라우저 편집기 실행 (기본)")
    s.add_argument("file", nargs="?", help="시작할 때 열 PDF 파일")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--no-browser", action="store_true", help="브라우저를 자동으로 열지 않음")
    s.add_argument("--debug", action="store_true")
    s.set_defaults(func=cmd_serve)

    s = sub.add_parser("desktop", help="브라우저 대신 자체 창으로 실행 (pywebview 필요)")
    s.add_argument("file", nargs="?", help="시작할 때 열 PDF 파일")
    s.set_defaults(func=lambda a: __import__("pdfeditor.desktop", fromlist=["main"]).main([a.file] if a.file else []))

    s = sub.add_parser("info", help="문서 정보")
    s.add_argument("file")
    s.set_defaults(func=cmd_info)

    s = sub.add_parser("extract", help="원하는 페이지만 추출")
    s.add_argument("file")
    s.add_argument("pages", help='페이지 범위. 예: "1-3,5,8-"')
    s.add_argument("-o", "--output", help="출력 파일 (또는 --each 사용 시 폴더)")
    s.add_argument("--each", action="store_true", help="페이지마다 별도 파일로 저장")
    s.set_defaults(func=cmd_extract)

    s = sub.add_parser("reorder", help="페이지 순서 변경")
    s.add_argument("file")
    s.add_argument("order", help='새 순서. 예: "3,1,2" (빠진 페이지는 뒤에 원래 순서대로 붙음)')
    s.add_argument("-o", "--output")
    s.set_defaults(func=cmd_reorder)

    s = sub.add_parser("delete", help="페이지 삭제")
    s.add_argument("file")
    s.add_argument("pages")
    s.add_argument("-o", "--output")
    s.set_defaults(func=cmd_delete)

    s = sub.add_parser("rotate", help="페이지 회전")
    s.add_argument("file")
    s.add_argument("pages")
    s.add_argument("--degrees", type=int, default=90, choices=[90, 180, 270, -90])
    s.add_argument("-o", "--output")
    s.set_defaults(func=cmd_rotate)

    s = sub.add_parser("add-text", help="텍스트 추가")
    s.add_argument("file")
    s.add_argument("page", type=int, help="페이지 번호 (1부터)")
    s.add_argument("--x", type=float, default=72, help="왼쪽에서의 위치 (pt)")
    s.add_argument("--y", type=float, default=72, help="위에서의 위치 (pt)")
    s.add_argument("--text", help="추가할 텍스트 (생략 시 표준입력)")
    s.add_argument("--size", type=float, default=12)
    s.add_argument("--color", default="#000000")
    s.add_argument("--width", type=float, help="자동 줄바꿈 폭 (pt)")
    s.add_argument("-o", "--output")
    s.set_defaults(func=cmd_add_text)

    s = sub.add_parser("replace", help="문자열 찾아 바꾸기")
    s.add_argument("file")
    s.add_argument("--find", required=True)
    s.add_argument("--replace", required=True)
    s.add_argument("--size", type=float)
    s.add_argument("--color")
    s.add_argument("-o", "--output")
    s.set_defaults(func=cmd_replace)

    s = sub.add_parser("blocks", help="페이지의 텍스트 블록 목록")
    s.add_argument("file")
    s.add_argument("page", type=int)
    s.set_defaults(func=cmd_blocks)

    s = sub.add_parser("edit-block", help="블록 번호로 텍스트 수정")
    s.add_argument("file")
    s.add_argument("page", type=int)
    s.add_argument("block", type=int)
    s.add_argument("--text", help="새 텍스트 (생략 시 표준입력)")
    s.add_argument("--size", type=float)
    s.add_argument("--color")
    s.add_argument("-o", "--output")
    s.set_defaults(func=cmd_edit_block)
    return parser


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    known = {"serve", "desktop", "info", "extract", "reorder", "delete", "rotate", "add-text", "replace", "blocks", "edit-block"}
    # 하위 명령 없이 실행하면 serve 로 간주 (python main.py [파일.pdf])
    if not argv or argv[0] not in known and not argv[0].startswith("-"):
        argv = ["serve"] + argv
    elif argv[0].startswith("-") and argv[0] not in ("-h", "--help"):
        argv = ["serve"] + argv
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except (ValueError, IndexError, FileNotFoundError) as e:
        print(f"오류: {e}", file=sys.stderr)
        sys.exit(1)

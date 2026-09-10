# PDF 편집기

PDF 파일의 **글을 수정하거나 새로 추가**하고, **페이지 순서를 바꾸고**, **원하는 페이지만 추출**할 수 있는 프로그램입니다.
브라우저에서 동작하는 편집 화면과 명령줄 도구를 함께 제공합니다. 한글 입력을 지원합니다.

## 주요 기능

| 기능 | 설명 |
|------|------|
| 글 수정 | 페이지의 텍스트 영역을 클릭해 내용·크기·색상을 바꾸거나 삭제 |
| 글 추가 | 원하는 위치를 클릭해 새 문장을 입력 (여러 줄, 자동 줄바꿈 가능) |
| 찾아 바꾸기 | 문서 전체에서 특정 문구가 들어 있는 줄을 한 번에 교체 |
| 페이지 순서 변경 | 왼쪽 썸네일을 드래그하거나 ▲▼ 버튼으로 이동 |
| 페이지 추출 | `1-3, 5, 8-` 처럼 범위를 지정해 새 PDF 로 저장 (페이지별 저장도 가능) |
| 그 외 | 페이지 삭제·회전·복제, 실행 취소/다시 실행, 확대/축소 |

## 설치 및 실행

Python 3.10 이상이 필요합니다.

```bash
pip install -r requirements.txt
python main.py              # 브라우저가 자동으로 열립니다 (http://127.0.0.1:8765)
python main.py 문서.pdf     # 파일을 바로 열면서 실행
```

Windows 는 `run.bat`, macOS/Linux 는 `./run.sh` 를 실행해도 됩니다 (가상환경을 자동으로 만들어 줍니다).

## 화면 사용법

1. **열기** 버튼으로 PDF 를 선택하거나 화면에 파일을 끌어다 놓습니다.
2. **글 수정**: 페이지에서 텍스트 위를 클릭하면 오른쪽에 편집 창이 열립니다. 내용을 고치고 **적용**을 누르세요.
   *텍스트 영역 표시* 버튼을 켜면 수정 가능한 영역이 점선으로 보입니다.
3. **글 추가**: 상단의 *글 추가* 를 켜고 페이지에서 글을 넣을 지점을 클릭한 뒤 내용을 입력합니다.
4. **순서 변경**: 왼쪽 썸네일을 끌어서 놓거나 ▲ ▼ 버튼을 누릅니다.
5. **페이지 추출**: 썸네일의 체크박스로 페이지를 고른 뒤 *페이지 추출* 을 누르면 범위가 자동으로 채워집니다.
   저장 경로를 비워 두면 브라우저로 다운로드되고, 경로를 적으면 그 위치에 바로 저장됩니다.
6. **저장 / 다운로드**: *다운로드* 는 편집본을 브라우저로 내려받고, *저장* 은 컴퓨터의 경로에 직접 씁니다.

단축키: `Ctrl+O` 열기, `Ctrl+Z` 실행 취소, `Ctrl+Y` 다시 실행, `Ctrl+Enter` 편집 적용, `Esc` 닫기, `PageUp/PageDown` 페이지 이동

## 명령줄 사용법

```bash
python main.py info 문서.pdf                                   # 문서 정보
python main.py extract 문서.pdf "1-3,5" -o 추출.pdf            # 페이지 추출
python main.py extract 문서.pdf "1-3" --each -o 출력폴더        # 페이지별로 따로 저장
python main.py reorder 문서.pdf "3,1,2" -o 순서변경.pdf        # 순서 변경 (빠진 페이지는 뒤에 붙음)
python main.py delete 문서.pdf "2,4" -o 삭제.pdf               # 페이지 삭제
python main.py rotate 문서.pdf "1" --degrees 90 -o 회전.pdf    # 페이지 회전
python main.py add-text 문서.pdf 1 --x 72 --y 72 --text "안녕하세요" --size 14 --color "#ff0000" -o 결과.pdf
python main.py replace 문서.pdf --find "기존 문구" --replace "새 문구" -o 결과.pdf
python main.py blocks 문서.pdf 1                               # 1페이지의 텍스트 블록 목록
python main.py edit-block 문서.pdf 1 0 --text "새 내용" -o 결과.pdf
```

`-o` 를 생략하면 원본 옆에 `_extracted`, `_edited` 같은 접미사가 붙은 파일로 저장됩니다.

## 파이썬에서 직접 사용

```python
from pdfeditor import PDFEditor

with PDFEditor("input.pdf") as ed:
    ed.replace_block(0, 0, "새 제목", color="#cc0000")       # 1페이지 첫 블록 수정
    ed.add_text(0, 72, 500, "추가한 문장", font_size=14)      # 좌표는 왼쪽 위 기준 (pt)
    ed.find_and_replace("주식회사", "(주)")
    ed.reorder_pages([2, 0, 1])
    ed.extract_pages_to("2-3", "일부.pdf")
    ed.save("output.pdf")
```

## 동작 방식과 한계

- 글 수정은 해당 영역의 원래 글자를 지운 뒤(레닥션) 새 글자를 같은 자리에 다시 쓰는 방식입니다.
  원본 폰트 대신 내장 CJK 폰트(Droid Sans Fallback)를 사용하므로 글꼴 모양이 원본과 조금 다를 수 있습니다.
- 새 글자가 원래 영역보다 길면 오른쪽 빈 공간까지 넓힌 뒤, 그래도 넘치면 글자 크기를 조금씩 줄여 맞춥니다.
- 스캔한 이미지 PDF 는 글자 정보가 없어 텍스트 수정 대상이 잡히지 않습니다. 글 추가·순서 변경·추출은 가능합니다.
- 암호가 걸린 PDF 는 먼저 암호를 해제해야 합니다.
- 저장 시 폰트를 서브셋 처리하여 파일 크기가 불필요하게 커지지 않도록 합니다.

## 테스트

```bash
pip install -r requirements-dev.txt
pytest
```

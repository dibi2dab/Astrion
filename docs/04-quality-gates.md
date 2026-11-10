# 품질 게이트

Astrion의 품질 판정은 **결정론적 100점 루브릭**입니다. 모델의 자기 평가, 확률적 판단, 외부 AI 호출은 없습니다. 모든 점수는 검사 가능한 규칙에서 나오며, 치명적 실패는 점수와 무관하게 발행을 막습니다.

## 1. 게이트가 실행되는 순서

```text
1. 스키마 검증        pydantic (models.py)
2. 교차 모듈 검증      validate.py       오류/경고
3. 품질 게이트         quality.py        PUBLISH / REVISE / BLOCK
4. 큐 페이로드 검증    queue.py          빌드 시
5. 발행 직전 재검증    queue.validate_payload
6. 발행 중복 방어      publisher._duplicate_reason
7. Blogger API 호출
```

한 단계라도 실패하면 다음 단계로 넘어가지 않습니다. 특히 **발행 직전 재검증**은 큐 파일이 손상되거나 누군가 수동으로 수정된 경우를 잡습니다.

## 2. 결정 규칙

```text
치명적 실패(fatal)가 하나라도 있으면  → BLOCK
점수 ≥ 85                            → PUBLISH
70 ≤ 점수 < 85                       → REVISE
점수 < 70                            → BLOCK
```

- `build`는 `PUBLISH`만 큐에 넣습니다.
- `REVISE`는 발행하지 않고, 왜 통과하지 못했는지 계획 출력에 남깁니다.
- `BLOCK`은 콘텐츠 수정 또는 검수 완료가 필요합니다.

임계값은 `config/astrion.yaml`에서 조정할 수 있습니다.

```yaml
quality:
  publish_threshold: 85
  revise_threshold: 70
```

> 임계값을 낮추는 것은 품질 기준을 낮추는 것입니다. 운영 중에는 가급적 콘텐츠를 고치는 방향을 권장합니다.

## 3. 치명적 실패 (fatal)

| 조건 | 메시지 예 | 이유 |
| --- | --- | --- |
| `review_status != approved` | `editorial review is not approved` | 사람의 최종 검수 없이는 발행 불가 |
| 단어 수 < 유형별 최소 | `only 512 words; science-explained needs at least 700` | 얕은 글 방지 |
| 미해결 중복 충돌 | `duplicate topic: <topic_id>` | 제목만 바꾼 재게시 방지 |
| HTML 호환성 실패 | `HTML compatibility: ...` | Blogger 삽입 시 깨질 HTML 차단 |
| HTML > 400 KB | `rendered HTML exceeds 400 KB` | Blogger/브라우저 성능 보호 |
| 필수 1차 출처 없음 | `content type requires a peer-reviewed, agency, or standards source` | 발견·분석 글의 근거 요구 |
| `editorial_synthesis`인데 출처 < 2 | `editorial synthesis requires at least two sources` | 종합 글의 근거 요구 |
| 보조 라벨 초과 | `too many secondary labels` | 레이블 정책 |

> 치명적 실패는 점수를 계산한 뒤에도 `BLOCK`으로 덮어씁니다. 예를 들어 98점이어도 검수 미완료면 발행되지 않습니다.

## 4. 경고 (warnings)

경고는 발행을 막지 않지만 품질 점수에 반영되고, `validate --strict`에서는 실패로 취급될 수 있습니다.

| 조건 | 메시지 |
| --- | --- |
| 출처 없음 | `no sources listed` |
| 오해 섹션 없음 | `no misconceptions section` |
| 키워드 밀도 초과 | `primary keyword density ... exceeds 2.50%` |
| 평균 문단 95단어 초과 | `average paragraph is long` |
| 읽기 시간 12분 초과 | `reading time above 12 minutes` |

## 5. 100점 루브릭

### 5.1 정확성·출처 — 25점

| 항목 | 점수 |
| --- | --- |
| `fact_basis`가 `textbook` / `peer_reviewed` / `agency_data` | 15 |
| `fact_basis`가 `editorial_synthesis` | 10 |
| 출처 0개 | 0 |
| 출처 1개 | 3 |
| 출처 2개 | 5 |
| 출처 3개 이상 | 7 |
| 1차 출처(`peer_reviewed`, `agency`, `standard`) 포함 | 3 |
| 출처는 있지만 1차 출처 없음 | 1 |
| 출처 없음 | 0 |

최대 25점. 출처 수와 종류를 함께 봅니다.

### 5.2 깊이·설명 — 20점

| 항목 | 점수 |
| --- | --- |
| 길이: `8 × min(1, words / min_words)` | 0~8 |
| 섹션 3개 이상 | 4 (스키마상 항상 충족) |
| 핵심 요약 3~5개 | 4 (스키마상 항상 충족) |
| 오해 섹션 또는 예제/표 존재 | 4 |
| 그 외 | 2 |

최대 20점. 최소 단어 수를 채우면 길이 점수는 만점입니다.

### 5.3 독창성·차별성 — 20점

| 항목 | 점수 |
| --- | --- |
| `unique_value` ≥ 120자 | 8 |
| `unique_value` 80~119자 | 6 |
| `unique_value` < 80자 | 3 (스키마상 불가) |
| 오해 섹션 존재 | 6 |
| 오해 섹션 없음 | 3 |
| 핵심 요약 3개 이상 | 3 |
| 미해결 중복 충돌 없음 | 3 |
| 미해결 중복 충돌 있음 | 0 (그리고 BLOCK) |

최대 20점.

### 5.4 가독성 — 15점

| 항목 | 점수 |
| --- | --- |
| 평균 문단 ≤ 75단어 | 5 |
| 평균 문단 76~95단어 | 3 |
| 평균 문단 > 95단어 | 0 |
| 평균 문장 ≤ 25단어 | 5 |
| 평균 문장 26~30단어 | 3 |
| 평균 문장 > 30단어 | 0 |
| 섹션당 80~400단어 | 5 |
| 섹션당 60~500단어 | 3 |
| 그 외 | 0 |

최대 15점. 평균에는 훅, 문단, 콜아웃, 목록, 오해 교정이 포함됩니다.

### 5.5 수학 표기 — 10점

| 조건 | 점수 |
| --- | --- |
| 수식이 없음 | 10 |
| 수식이 있음 + 모든 `equation`에 기호 정의 | 5 |
| 수식이 있음 + LaTeX 균형(`\(`/`\)`, `\[`/`\]`, `{}`) | 5 |

수식이 있는 글은 두 항목을 모두 만족해야 10점입니다. `equation`의 기호 정의는 스키마가 강제하므로, 실제 감점은 주로 LaTeX 균형에서 발생합니다.

### 5.6 Blogger HTML — 5점

| 조건 | 점수 |
| --- | --- |
| HTML 검사 통과 | 5 |
| HTML 검사 실패 | 0 (그리고 BLOCK) |

### 5.7 SEO — 5점

| 항목 | 점수 |
| --- | --- |
| `primary_keyword`가 제목에 포함 | 2 |
| `primary_keyword`가 본문 앞 120단어에 포함 | 1 |
| 키워드 밀도 0.1%~2.5% | 1 |
| `meta_description` 길이 80~160자 | 1 (스키마상 항상 충족) |

최대 5점. 키워드 밀도 상한은 `quality.max_keyword_density`(기본 0.025)입니다.

### 5.8 합계

```text
accuracy_sources   25
depth_explanation  20
distinct_value     20
readability        15
notation           10
blogger_html        5
seo                 5
--------------------
total             100
```

## 6. HTML 검사 규칙

`quality.check_html()`은 Blogger 게시물 조각으로서의 안전성을 검사합니다.

### 허용되지 않는 것

- `<html>`, `<head>`, `<body>` — Blogger가 문서를 소유합니다.
- `<h1>` — 게시물 제목이 유일한 H1입니다.
- 닫히지 않은 태그, 짝이 맞지 않는 닫는 태그
- `href` 없는 `<a>`
- `alt` 없는 `<img>`
- 중복 `id`
- 인라인 스타일의 고정 픽셀 너비(`width: 900px`, `min-width: 900px`)
- MathJax CDN(`cdn.jsdelivr.net/npm/mathjax@3/`)이 아닌 외부 스크립트
- `MathJax` 문자열이 없는 인라인 스크립트

### 허용되는 것

- MathJax 3 설정 인라인 스크립트
- `https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-svg.js`
- 반응형을 위한 `%`, `max-width`, `min-width: 420px` 같은 표 내부 스타일
  - 단, 검사기는 `width: <숫자>px`와 `min-width: <숫자>px`를 금지합니다. 표의 `min-width: 420px`는 CSS 클래스에 있으므로 인라인 검사 대상이 아닙니다.

## 7. 중복 판정

중복은 **편집 서명**을 중심으로 판정합니다.

```text
signature = core_question + " " + explanation_target + " " + central_principle
```

### 비교 신호

| 신호 | 임계값 | 점수 기여 |
| --- | --- | --- |
| 같은 `topic.id` | — | 1.0 |
| 서명 토큰 Jaccard | ≥ 0.70 | 실제 유사도 |
| 서명 문자열 유사도 | ≥ 0.85 | 실제 유사도 |
| 제목 유사도 | ≥ 0.92 | 실제 유사도 |
| 같은 `primary_keyword` | — | 0.75 |
| 공유 `alias` | — | 0.80 |

- 신호가 하나라도 있으면 충돌 후보입니다.
- `score`는 관측된 신호 중 최댓값입니다.
- `distinct_from`에 명시한 상대는 `overridden`으로 표시되어 미해결 충돌에서 제외됩니다.
- `distinct_from`은 **명시한 상대에게만** 적용됩니다.

### 적용 지점

1. `validate`: 활성 모듈끼리 충돌하면 오류, 비활성/예외면 경고.
2. `quality`: 발행 이력과 충돌하면 치명적 실패.
3. `planner`: 같은 날 선택된 주제끼리 충돌하면 후보 제외.
4. `publisher`: 발행 직전 상태 원장과 다시 비교.

### 발행 직전 중복 방어

`publisher._duplicate_reason()`은 다음을 확인합니다.

- 같은 `topic_id`가 이미 발행됨
- 같은 편집 서명
- 같은 `primary_keyword`
- 공유 `alias`

하나라도 걸리면 API를 호출하지 않고 실패 아카이브로 보냅니다.

## 8. 품질 보고서 읽기

`plan --json`은 각 항목의 `quality_score`와 `decision`을 보여줍니다.

```bash
python -m astrion plan --date 2026-11-10 --json
```

전체 breakdown은 큐 페이로드에 들어갑니다.

```bash
python -m astrion build --date 2026-11-10
cat data/queue/2026-11-10-s1-*.json
```

`quality` 객체 예:

```json
{
  "score": 98.0,
  "decision": "PUBLISH",
  "breakdown": {
    "accuracy_sources": 25.0,
    "depth_explanation": 20.0,
    "distinct_value": 20.0,
    "readability": 15.0,
    "notation": 10.0,
    "blogger_html": 5.0,
    "seo": 3.0
  },
  "fatal": [],
  "warnings": [],
  "metrics": {
    "words": 1180,
    "min_words": 700,
    "avg_paragraph_words": 52.4,
    "avg_sentence_words": 18.1,
    "keyword_density": 0.0042,
    "sources": 3,
    "primary_sources": 1,
    "reading_minutes": 6,
    "html_bytes": 18234,
    "has_math": true,
    "tables": 1,
    "scripts": 2,
    "links": 4
  }
}
```

특정 주제만 빠르게 평가하려면:

```bash
python -c "from astrion.config import load_settings; from astrion.loader import load_topics; from astrion.quality import evaluate; s=load_settings(); t=next(x.topic for x in load_topics(s.content_dir) if x.topic.id=='math.division-by-zero'); import json; print(json.dumps(evaluate(t,s).to_dict(), indent=2))"
```

## 9. 큐 페이로드 재검증

`validate_payload()`는 발행 직전에 다음을 확인합니다.

- `schema == "astrion.queue/v1"`
- `queue_id` 형식 (`YYYY-MM-DD-sN-<slug>`)
- `topic_id` 형식
- `publish_date`, `publish_after` 파싱 가능
- `post.html` 존재 + `html_sha256` 일치
- 제목 20~80자, 메타 설명 80~160자
- 한글 없음
- HTML 검사 통과, 400 KB 이하
- `quality.decision == "PUBLISH"` 그리고 점수 ≥ 임계값
- 첫 번째 라벨이 9개 주제 영역 중 하나
- 라벨 수 ≤ 1 + `max_secondary_labels`
- `review_status == "approved"`
- `status == "queued"`

하나라도 실패하면 실패 아카이브 + `state.failed` 기록 + Issue(워크플로 실패 시)로 이어집니다.

## 10. 점수를 올리는 방법

| 낮은 항목 | 개선 |
| --- | --- |
| `accuracy_sources` | 신뢰할 수 있는 출처 추가, 가능하면 1차 출처 포함 |
| `depth_explanation` | 최소 단어 수 충족, 예제/표 또는 오해 섹션 추가 |
| `distinct_value` | `unique_value`를 120자 이상으로 구체화, 오해 섹션 추가 |
| `readability` | 긴 문단 분할, 문장 길이 축소, 섹션당 단어 수 조정 |
| `notation` | `equation`에 기호 정의, LaTeX 괄호/중괄호 균형 확인 |
| `blogger_html` | `<h1>`/`<html>` 제거, 태그 닫기, `alt`/`href` 채우기 |
| `seo` | 키워드를 제목과 도입부에 자연스럽게 포함, 메타 길이 확인 |

## 11. 품질 게이트를 우회하지 않는 이유

- `build`는 `PUBLISH`가 아닌 항목을 큐에 넣지 않습니다.
- `publish`는 큐를 다시 검증합니다.
- 워크플로는 `validate --strict`를 먼저 실행합니다.
- 상태 원장과 Blogger 마커로 중복을 다시 막습니다.

즉, 한 곳에서 실수해도 다음 단계에서 막힙니다. 이 중복 방어는 의도된 설계이며, 우회하려면 코드를 수정해야 합니다. 그 자체가 리뷰 대상입니다.

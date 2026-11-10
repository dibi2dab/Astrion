# 콘텐츠 모델

하나의 YAML 파일이 하나의 Blogger 게시물이 됩니다. 이 문서는 `astrion.topic/v1` 스키마의 모든 필드와 검증 규칙을 설명합니다.

## 1. 파일 위치와 이름

```text
content/
  mathematics/
    why-dividing-by-zero-is-undefined.yaml
  physics/
    why-the-sky-is-blue-rayleigh-scattering-explained.yaml
```

- 확장자는 `.yaml` 또는 `.yml`입니다.
- 파일명은 `slug`와 같아야 합니다. 다르면 `filename_mismatch` 경고가 발생합니다.
- 같은 `id`, `slug`, `title`, `alias`는 프로젝트 전체에서 유일해야 합니다.
- 디렉터리 구조는 자유롭게 나눌 수 있지만, `main_category`와 무관하게 로더는 모든 하위 YAML을 읽습니다.

## 2. 최상위 필드

| 필드 | 필수 | 제약 | 설명 |
| --- | --- | --- | --- |
| `schema` | 아니오 | `astrion.topic/v1` | 생략 시 기본값 |
| `id` | 예 | 소문자, 점 포함 (`math.division-by-zero`) | 안정적인 주제 식별자. 변경 금지 |
| `status` | 아니오 | `draft`, `active`, `paused` | 기본 `draft`. `active`만 발행 후보 |
| `content_type` | 예 | 9종 enum | 글의 목적 |
| `main_category` | 예 | 9종 enum | 주제 영역. 첫 Blogger 레이블 |
| `labels` | 아니오 | 최대 4개 | 보조 Blogger 레이블 |
| `title` | 예 | 20~80자 | Blogger 게시물 제목 |
| `seo_title` | 아니오 | 20~80자 | 비우면 `title` 사용 |
| `slug` | 예 | 소문자 kebab-case, ≤ 80자 | URL/파일명 기준 |
| `meta_description` | 예 | 80~160자 | 큐/품질용. Blogger에 자동 전송되지 않음 |
| `primary_keyword` | 예 | 3~80자 | 중복·SEO 판정의 핵심 |
| `secondary_keywords` | 아니오 | 최대 8개 | 보조 검색어 |
| `hook` | 예 | 60~1200자 | 첫 문단 |
| `core_question` | 예 | 30~400자 | 글이 답하는 핵심 질문 |
| `explanation_target` | 예 | 40~500자 | 설명 대상 |
| `central_principle` | 예 | 40~600자 | 글을 관통하는 중심 원리 |
| `unique_value` | 예 | 80~900자 | 이 글만의 차별점 |
| `key_takeaways` | 예 | 3~5개 | 핵심 요약 |
| `sections` | 예 | 3~10개 | 본문 |
| `misconceptions` | 아니오 | 최대 8개 | 흔한 오해와 교정 |
| `sources` | 아니오 | 최대 12개 | 본문 근거 |
| `further_reading` | 아니오 | 최대 8개 | 심화 자료 |
| `related` | 아니오 | 최대 8개 | 관련 주제 ID |
| `aliases` | 아니오 | 최대 10개 | 중복 판정용 다른 이름 |
| `distinct_from` | 아니오 | 최대 8개 | 명시적 중복 예외 |
| `provenance` | 예 | 객체 | 작성·검수 메타데이터 |
| `priority` | 아니오 | 1~5, 기본 3 | 클수록 먼저 선택 |
| `evergreen` | 아니오 | 불리언, 기본 `true` | 시간 민감도 표시 |
| `notes` | 아니오 | ≤ 1000자 | 편집 메모. 발행되지 않음 |

### ID 규칙

```text
^[a-z][a-z0-9-]*(?:\.[a-z0-9][a-z0-9-]*)+$
```

- 소문자로 시작합니다.
- 점(`.`)으로 구분된 두 개 이상의 세그먼트가 필요합니다.
- 예: `math.division-by-zero`, `physics.rayleigh-scattering-sky`, `biology.crispr-off-target-effects`
- 한 번 발행된 ID는 바꾸지 않습니다. ID는 상태 원장과 중복 판정의 기준입니다.

## 3. 열거형

### `main_category`

| 값 | 표시 이름 |
| --- | --- |
| `Mathematics` | Mathematics |
| `Physics` | Physics |
| `Chemistry` | Chemistry |
| `Biology & Life Sciences` | Biology & Life Sciences |
| `Space & Astronomy` | Space & Astronomy |
| `Earth & Natural Sciences` | Earth & Natural Sciences |
| `Computing & Formal Sciences` | Computing & Formal Sciences |
| `Engineering & Applied Sciences` | Engineering & Applied Sciences |
| `Emerging & Interdisciplinary Science` | Emerging & Interdisciplinary Science |

### `content_type`

`latest-discovery`, `scientific-breakthrough`, `unsolved-problem`, `mathematics-explained`, `science-explained`, `research-analysis`, `experiments-data-modeling`, `emerging-science`, `basic-knowledge`

### `source.kind`

`textbook`, `peer_reviewed`, `agency`, `standard`, `reference`, `data`

1차 출처로 인정되는 종류는 `peer_reviewed`, `agency`, `standard`입니다.

## 4. `sections`와 블록

```yaml
sections:
  - heading: Section heading
    blocks:
      - type: paragraph
        text: A paragraph of English prose.
```

### Section

| 필드 | 제약 |
| --- | --- |
| `heading` | 3~140자, 같은 글 안에서 유일 |
| `blocks` | 1~40개 |

### 블록 타입

#### `paragraph`

```yaml
- type: paragraph
  text: Text of the paragraph.
```

- `text`는 1자 이상이며 한글을 포함할 수 없습니다.

#### `math`

```yaml
- type: math
  latex: 'A_{\mathrm{path}} = \frac{A_{0}}{\cos z}'
  display: true
  caption: Air mass grows with the zenith angle.
```

- `latex`: 1자 이상
- `display`: 기본 `true`
- `caption`: 선택

#### `equation`

```yaml
- type: equation
  latex: 'I_{\mathrm{scattered}} \propto \frac{1}{\lambda^{4}}'
  variables:
    - symbol: I_scattered
      meaning: intensity of redirected light
    - symbol: \lambda
      meaning: wavelength of the incident light
      unit: nanometers
  caption: The inverse fourth-power law.
```

- `latex`: 1자 이상
- `variables`: 1~10개, 필수
- 각 변수: `symbol`(1~24자), `meaning`(3~160자), `unit`(선택, ≤ 40자)
- `caption`: 선택

#### `list`

```yaml
- type: list
  ordered: false
  items:
    - First item.
    - Second item.
```

- `items`: 1~12개
- `ordered`: 기본 `false`

#### `table`

```yaml
- type: table
  caption: Relative scattering strength
  headers: [Color, Wavelength, Relative scattering]
  rows:
    - [Blue, 450 nm, "5.8"]
    - [Red, 700 nm, "1.0"]
```

- `caption`: 3~200자, 필수
- `headers`: 2~6개
- `rows`: 1~30행
- 모든 행의 셀 수가 같아야 하고, `headers` 수와도 같아야 합니다.
- 렌더링 시 가로 스크롤 래퍼가 붙습니다.

#### `callout`

```yaml
- type: callout
  tone: note
  title: Color is not a substance
  text: The blue is produced by scattering, not by a blue gas.
```

- `tone`: `key`, `note`, `warning`
- `title`: 선택, ≤ 120자
- `text`: 1자 이상

#### `example`

```yaml
- type: example
  title: A short calculation
  steps:
    - Compare blue at 450 nanometers with red at 700 nanometers.
    - Raise 700 divided by 450 to the fourth power.
  conclusion: Blue is scattered roughly six times as strongly as red.
```

- `title`: 3~160자
- `steps`: 1~12개
- `conclusion`: 선택

## 5. `misconceptions`

```yaml
misconceptions:
  - claim: The sky is blue because it reflects the ocean.
    correction: >-
      The sky is blue over deserts and continents too, and reflection does not
      explain red sunsets. Air molecules scatter short wavelengths across the sky.
```

| 필드 | 제약 |
| --- | --- |
| `claim` | 5~240자 |
| `correction` | 20~1200자 |

## 6. `sources`와 `further_reading`

```yaml
sources:
  - id: nasa-space-place-blue-sky
    title: Why Is the Sky Blue?
    publisher: NASA Space Place
    url: https://spaceplace.nasa.gov/blue-sky/en/
    kind: agency
    accessed_at: 2026-11-10
```

| 필드 | 필수 | 제약 |
| --- | --- | --- |
| `id` | 예 | `^[a-z0-9][a-z0-9-]{0,40}$`, 글 안에서 유일 |
| `title` | 예 | 3~300자 |
| `publisher` | 예 | 2~160자 |
| `url` | 예 | 절대 `http(s)` URL, 10~500자 |
| `kind` | 아니오 | 기본 `reference` |
| `published_at` | 아니오 | 날짜 |
| `accessed_at` | 예 | 날짜 |
| `doi` | 아니오 | `10.xxxx/suffix` 형식, ≤ 120자 |
| `note` | 아니오 | ≤ 300자 |

- `example.com`, `localhost` 등 자리표시자 호스트는 거부됩니다.
- 호스트에 점(`.`)이 있어야 합니다.
- `sources`와 `further_reading` 전체에서 `id`가 중복되면 안 됩니다.
- `accessed_at`이 미래이면 경고가 발생합니다.

## 7. `provenance`

```yaml
provenance:
  authored_by: Astrion editorial
  fact_basis: textbook
  reviewed_at: 2026-11-10
  review_status: approved
  notes: Seed article for pipeline validation.
```

| 필드 | 제약 |
| --- | --- |
| `authored_by` | 2~120자, 기본 `Astrion editorial` |
| `fact_basis` | `textbook`, `peer_reviewed`, `agency_data`, `editorial_synthesis` |
| `reviewed_at` | 날짜, 필수 |
| `review_status` | `draft`, `reviewed`, `approved` (기본 `draft`) |
| `notes` | ≤ 1000자 |

발행에는 `review_status: approved`가 필요합니다.

## 8. 중복 관련 필드

```yaml
aliases:
  - blue sky
  - rayleigh scattering

distinct_from:
  - topic_id: physics.sunsets-and-atmospheric-optics
    note: >-
      This module focuses on the spectrum of the low Sun, while the referenced
      module explains the daytime sky. The central questions differ.
```

- `aliases`: 다른 이름으로 검색될 수 있는 표현. 같은 별칭을 가진 두 주제는 충돌합니다.
- `distinct_from[].note`: 40~600자. 왜 중복이 아닌지 사람이 읽을 수 있어야 합니다.
- 예외는 `topic_id`로 명시한 상대에게만 적용됩니다.

## 9. `related`

```yaml
related:
  - physics.rayleigh-scattering-sky
```

- 존재하는 주제 ID만 허용됩니다. 없으면 검증 오류입니다.
- 자기 자신을 참조할 수 없습니다.
- 대상 주제가 **이미 발행되어 상태에 URL이 기록된 경우에만** "Related reading" 링크가 렌더링됩니다.

## 10. 완전한 골격

```yaml
schema: astrion.topic/v1
id: physics.example-topic
status: active
content_type: science-explained
main_category: Physics
labels:
  - Light and Optics
  - Example Label
title: A Clear Question About a Physical Phenomenon
slug: a-clear-question-about-a-physical-phenomenon
meta_description: >-
  A concise explanation of the phenomenon, the central principle behind it, and
  the evidence that supports the explanation in this article.
primary_keyword: a clear question
secondary_keywords:
  - related term
hook: >-
  An opening paragraph that frames the question, states why it matters, and
  previews the principle that will explain it.
core_question: >-
  What causes the phenomenon, and why does it behave the way it does?
explanation_target: >-
  The mechanism, the relevant physical quantities, and the observations that
  distinguish this explanation from alternatives.
central_principle: >-
  One principle that organizes the entire explanation and predicts the observed
  behavior across the cases discussed in the article.
unique_value: >-
  This article connects the principle to a worked example, resolves a common
  misconception, and explains a related observation that a short summary would
  miss entirely.
key_takeaways:
  - The first takeaway states the main idea in one sentence.
  - The second takeaway adds a quantitative relationship.
  - The third takeaway corrects a common misunderstanding.
sections:
  - heading: The Observation
    blocks:
      - type: paragraph
        text: >-
          Describe the observation in concrete terms and establish what needs to
          be explained.
  - heading: The Mechanism
    blocks:
      - type: paragraph
        text: >-
          Introduce the central principle and connect it to the observation.
      - type: equation
        latex: 'y = f(x)'
        variables:
          - symbol: y
            meaning: the quantity being explained
          - symbol: x
            meaning: the controlling variable
        caption: The relationship that drives the explanation.
  - heading: What It Predicts
    blocks:
      - type: paragraph
        text: >-
          Show how the principle predicts a second observation and where the
          explanation stops applying.
misconceptions:
  - claim: A common but incorrect explanation.
    correction: >-
      Explain why the incorrect explanation fails and what the evidence actually
      shows, in enough detail to be useful to a careful reader.
sources:
  - id: example-reference
    title: A Reliable Reference Work
    publisher: A Reputable Publisher
    url: https://www.example.org/reference
    kind: reference
    accessed_at: 2026-11-10
related: []
aliases:
  - alternate name
distinct_from: []
provenance:
  authored_by: Astrion editorial
  fact_basis: textbook
  reviewed_at: 2026-11-10
  review_status: approved
  notes: ""
priority: 3
evergreen: true
notes: ""
```

> 위 예시의 `https://www.example.org`는 설명용입니다. 실제 모듈에서는 `example.com` 계열 호스트가 거부되므로 실제 출처 URL을 사용해야 합니다.

## 11. 흔한 검증 오류

| 메시지 | 원인 | 해결 |
| --- | --- | --- |
| `Astrion posts are English-only` | 한글 포함 | 영어로 교체 |
| `prose contains a placeholder marker` | `TODO`, `TBD`, `[insert ...]` 등 | 실제 문장으로 교체 |
| `slug must already be lowercase kebab-case` | 대문자·공백·언더스코어 | 소문자 kebab-case로 수정 |
| `every table row must have the same number of cells` | 표 행 길이 불일치 | 셀 수 맞춤 |
| `table headers and rows must have the same number of columns` | 헤더/행 불일치 | 열 수 맞춤 |
| `do not repeat the main category in labels` | 라벨에 주제 영역 반복 | 다른 보조 라벨 사용 |
| `section headings must be unique` | 섹션 제목 중복 | 제목 변경 |
| `related topic '...' does not exist` | 없는 ID 참조 | ID 수정 또는 모듈 추가 |
| `a topic cannot relate to itself` | 자기 참조 | 제거 |
| `source url must not be a placeholder` | example.com/localhost | 실제 URL |
| `doi must look like 10.xxxx/suffix` | DOI 형식 오류 | `10.`으로 시작하는 DOI |
| `editorial review is not approved` (품질) | 검수 미완료 | 검수 후 `approved` |

## 12. 검증 실행

```bash
python -m astrion validate
python -m astrion validate --strict
python -m astrion validate --json
python -m astrion list
python -m astrion check-duplicates
```

- `validate`는 스키마 + 교차 모듈 검증을 수행합니다.
- `--strict`는 경고도 실패로 처리합니다. CI는 이 모드를 사용합니다.
- `build`는 검증 오류가 있으면 아무것도 쓰지 않고 중단합니다.

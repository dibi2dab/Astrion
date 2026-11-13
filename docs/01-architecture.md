# 아키텍처

Astrion은 **사람이 작성·검수한 YAML 콘텐츠 모듈**을 결정론적으로 Blogger HTML로 변환하고, GitHub Actions에서 **저장된 Google 브라우저 세션으로 Blogger 웹 에디터를 조작해** 발행하는 파이프라인입니다. 콘텐츠 생성, 품질 판단, 발행 판단 어디에도 외부 AI API를 사용하지 않습니다.

## 1. 설계 원칙

1. **No AI API**: 주제 선택, 본문, 품질 점수, 발행 여부는 규칙과 사람의 검수로만 결정됩니다.
2. **결정론**: 같은 모듈, 같은 설정, 같은 상태라면 같은 HTML, 같은 품질 점수, 같은 큐 페이로드가 나옵니다. 난수, 시각 의존 로직, 네트워크 의존 로직을 렌더링 경로에서 배제합니다.
3. **단일 책임 모듈**: 로딩, 검증, 중복 판정, 품질, 렌더링, 계획, 빌드, 큐, 발행, 상태를 별도 파일로 분리합니다.
4. **실패는 시끄럽게**: 실패한 페이로드는 삭제하지 않고 `data/archive/failed/`에 보존하며, GitHub Actions는 Issue를 엽니다.
5. **멱등 발행**: 같은 큐 항목을 두 번 발행하지 않습니다. 로컬 상태 원장과 제목/마커 기반 재조정을 함께 사용합니다.
6. **UI 변경 격리**: 웹 에디터 선택자를 `web/selectors.json`에 모으고, 매일 자동 검사·추론해 PR로 반영합니다.
7. **API fallback**: OAuth 경로는 `blogger.backend: api`로 명시할 때만 사용합니다.

## 2. 전체 흐름

```text
content/**/*.yaml
      │
      ▼
 loader.load_topics          YAML → Topic(pydantic)
      │
      ▼
 validate.validate_topics    교차 모듈 검증 + 중복 후보
      │
      ▼
 planner.build_plan          품질 게이트 + 슬롯 배정 + 중복 회피
      │
      ▼
 builder.build               payload 생성 + state 예약 + preview
      │
      ▼
 data/queue/*.json           발행 대기 계약(astrion.queue/v1)
      │
      ▼
 publisher.publish_due       재검증 → 예약 → 발행 → 기록 → 아카이브
      │
      ▼
 web_publisher.py            Node 브리지 + 오류 매핑
      │
      ▼
 web/*.mjs (Playwright)      Blogger 웹 에디터 (draft 또는 live)
```

### 단계별 책임

| 단계 | 파일 | 하는 일 | 하지 않는 일 |
| --- | --- | --- | --- |
| 로딩 | `astrion/loader.py` | YAML 파싱, pydantic 검증, ID 중복 확인 | 파일 수정 |
| 검증 | `astrion/validate.py` | 슬러그/제목/별칭 중복, 출처, 관련 주제, 발행 이력 충돌 | 품질 점수 계산 |
| 중복 | `astrion/duplicate.py` | 편집 서명·키워드·제목·별칭 비교 | 콘텐츠 수정 |
| 품질 | `astrion/quality.py` | 100점 루브릭, 치명/경고, HTML 검사 | 임계값 자동 조정 |
| 렌더링 | `astrion/render.py` | Jinja2 템플릿으로 Blogger fragment 생성 | 상태 변경 |
| 계획 | `astrion/planner.py` | 후보 필터, 품질 통과, 슬롯/카테고리 배분 | 파일 쓰기 |
| 빌드 | `astrion/builder.py` | 큐 페이로드 저장, 미리보기, 상태 예약 | Blogger 호출 |
| 큐 | `astrion/queue.py` | 페이로드 계약, 검증, due 필터, 아카이브 | 품질 판정 |
| 발행 | `astrion/publisher.py` | 멱등 발행, 재시도, in-flight 조정, 중복 방어 | HTML 생성 |
| 웹 브리지 | `astrion/web_publisher.py` | 세션 복원, Node 실행, sentinel 파싱, 오류 매핑 | DOM 조작 |
| 웹 엔진 | `web/*.mjs` | 제목/본문/라벨 입력, 초안 저장, 라이브 발행, 선택자 검사 | 품질/중복 판단 |
| API fallback | `astrion/blogger.py` | OAuth refresh, 재시도, 응답 형태 검증, 계약 비교 | 발행 정책 결정 |
| 상태 | `astrion/state.py` | 대기/발행/실패 원장, 슬롯/쿼터 조회 | Blogger 상태 조회 |
| CLI | `astrion/cli.py` | 명령 파싱, 오케스트레이션, 종료 코드 | 비즈니스 규칙 구현 |

## 3. 콘텐츠 모듈

- 위치: `content/<category>/<slug>.yaml`
- 스키마: `astrion.topic/v1`
- 하나의 모듈 = 하나의 Blogger 게시물
- 파일명은 `slug`와 일치해야 합니다(불일치 시 경고).
- 상세 필드는 [콘텐츠 모델](02-content-model.md)을 참고합니다.

## 4. 렌더링

`render.render_post()`는 다음을 수행합니다.

- Jinja2 템플릿(`astrion/templates/post.html.j2`)으로 HTML fragment 생성
- `<div class="astrion-post" data-astrion-topic="...">`로 시작하는 Blogger 삽입용 조각
- `<html>`, `<head>`, `<body>`, `<h1>`은 생성하지 않음 (Blogger가 게시물 제목을 H1로 제공)
- 반응형 표(`.astrion-table-wrap`), 콜아웃, 수식, 예제, 오해, 출처, 관련 글 렌더링
- 수식이 있을 때만 MathJax 3 설정과 CDN 스크립트 포함
- 모든 텍스트는 HTML escape, 출처 링크는 `target="_blank" rel="noopener noreferrer"`

렌더링 결과는 `RenderedPost`에 HTML, 순수 텍스트, 단어 수, 읽기 시간, 수식 여부로 정리됩니다.

## 5. 큐 페이로드

`data/queue/<queue_id>.json`은 빌드와 발행 사이의 계약입니다.

```text
schema        astrion.queue/v1
queue_id      YYYY-MM-DD-sN-<slug>
topic_id      모듈 ID
status        queued
publish_date  America/New_York 기준 날짜
publish_after 절대 시각(오프셋 포함)
slot          0부터 시작하는 슬롯 인덱스
blogger       is_draft, labels
post          title, html, html_sha256, meta_description, primary_keyword, ...
fingerprint   signature, sha256, primary_keyword, aliases
quality       점수, 결정, breakdown, fatal, warnings, metrics
review        review_status, fact_basis, reviewed_at, authored_by
sources       발행에 사용한 출처 목록
```

발행 직전에 `validate_payload()`가 다시 검사합니다. HTML 체크섬이 맞지 않거나, 품질 결정이 `PUBLISH`가 아니거나, 검수 상태가 `approved`가 아니면 API를 호출하지 않고 실패 아카이브로 보냅니다.

## 6. 상태 원장

`data/state.json`은 GitHub Actions가 커밋하는 멱등성 원장입니다.

```text
schema     astrion.state/v1
updated_at 마지막 저장 시각
published  발행 완료 기록 (post id, url, signature, keyword, aliases ...)
pending    큐 예약 기록 (status: queued / in_flight, attempts, next_attempt_at)
failed     실패 기록 (last_error, failed_at)
builds     빌드 이력
```

- Blogger가 게시물 ID·URL·실제 상태의 **원천**입니다.
- 로컬 상태는 "무엇을 이미 시도했는가"를 기록하는 **원장**입니다.
- `pending`과 `published`는 `queue_id`와 `topic_id` 기준으로 중복을 제거합니다.
- `used_slots`, `count_for_date`, `categories_for_date`로 하루 쿼터와 슬롯을 계산합니다.

## 7. 아카이브

```text
data/archive/
  published/YYYY-MM/<queue_id>.json   성공 페이로드 + publication 기록
  published/YYYY-MM/<queue_id>.html   실제 발행 HTML
  failed/YYYY-MM/<queue_id>.json      실패 페이로드 + error
  failed/YYYY-MM/<queue_id>.html      실패 시점 HTML
  failed/YYYY-MM/<queue_id>.error.txt 전체 오류 메시지
```

성공/실패 모두 큐에서 제거되지만, 실패 파일은 절대 조용히 사라지지 않습니다. `__path` 같은 내부 키는 아카이브에서 제거됩니다.

## 8. 웹 에디터 변경 대응

Blogger 에디터 DOM이 바뀌어도 코드가 스스로 재작성되지는 않습니다. 대신 **감지 → 추론 → PR/Issue** 순서로 대응합니다.

1. **중앙 선택자 맵**: `web/selectors.json`에 각 컨트롤의 `primary`와 `candidates`를 저장합니다.
2. **발행 전 healthcheck**: `publish.yml`이 `astrion healthcheck`로 세션과 필수 컨트롤을 먼저 확인합니다.
3. **일일 건강검진**: `web-health.yml`이 `npm run selectors:check`를 실행하고, 깨진 항목은 `npm run selectors:fix`로 라이브 DOM에서 추론합니다.
4. **자동 PR**: 추론 결과가 `web/selectors.json`을 바꾸면 PR을 만듭니다.
5. **세션 만료 Issue**: 세션이 만료되었으면(종료 코드 3) Issue를 열고 복구 절차를 안내합니다.
6. **실패 Issue**: 발행 실패도 Issue로 열리며, 워크플로가 실패하면 `web-health.yml`이 즉시 후속 실행됩니다.

> "알아서 코드를 고치는" 자기 수정은 하지 않습니다. 변경을 **자동으로 감지하고, 근거가 있을 때만 선택자 PR을 제안하며, 확신이 없으면 발행을 안전하게 멈추는** 방식입니다.

### API fallback의 계약 감시

`blogger.backend: api`를 사용할 때는 기존 discovery 계약 감시가 그대로 동작합니다.

1. `config/blogger_contract.json`에 `posts` 메서드/파라미터와 SHA-256을 저장합니다.
2. `healthcheck --strict-contract`가 계약 변경을 감지하면 발행을 중단합니다.
3. `blogger-contract.yml`이 매주 변경 시에만 PR을 만듭니다.

## 9. GitHub Actions

| 워크플로 | 트리거 | 역할 | 권한 |
| --- | --- | --- | --- |
| `ci.yml` | push/PR/manual | Python 테스트, Node 문법 검사, `validate --strict`, `build --dry-run` | `contents: read` |
| `build.yml` | 매일 05:10 UTC, manual | 다음 슬롯 큐 생성, 상태/큐 커밋 | `contents: write` |
| `publish.yml` | 매시간 7분, manual | Playwright 설치, 세션 healthcheck, due 항목 발행, 커밋, 실패 Issue | `contents: write`, `issues: write` |
| `web-health.yml` | 매일 09:00 UTC, manual, publish 실패 후 | 에디터 선택자 검사/추론, PR 또는 세션 Issue | `contents: write`, `pull-requests: write`, `issues: write` |
| `blogger-contract.yml` | 매주 월 06:17 UTC, manual | (API fallback) 계약 갱신 PR, 위반 시 Issue | `contents: write`, `pull-requests: write`, `issues: write` |

`build.yml`과 `publish.yml`은 같은 큐/상태 파일을 수정하므로 `concurrency.group: astrion-state`를 공유합니다. 두 워크플로가 동시에 실행되지 않습니다.

시간대:

- 빌드는 05:10 UTC에 실행됩니다. 이는 `00:10 EST` 또는 `01:10 EDT`로, 두 경우 모두 08:30 슬롯 이전입니다.
- 발행은 매시간 7분에 실행되며, 큐의 절대 시각 `publish_after`와 비교해 due 항목만 처리합니다. 따라서 DST가 바뀌어도 크론을 두 벌 관리할 필요가 없습니다.
- 슬롯 시각이 이미 지난 경우 `publish_after`는 현재 시각 + 3분(항목별 +2분)으로 올려 과거 발행을 피합니다.

## 10. 보안과 비밀

- 웹 발행 세션은 GitHub Secrets에만 저장합니다: `BLOGGER_BLOG_ID`, `BLOGGER_STORAGE_STATE`.
- API fallback 자격 증명은 `BLOGGER_CLIENT_ID`, `BLOGGER_CLIENT_SECRET`, `BLOGGER_REFRESH_TOKEN`에만 저장합니다.
- `astrion login`/`npm run login`은 로컬에서만 실행하며 `.state/`에 세션을 저장하고 Base64 값을 출력합니다.
- `.gitignore`는 `credentials/`, `secrets/`, `token.json`, `*.service-account.json`, `.state/`, `*_storage_state.json`, `artifacts/`를 제외합니다.
- 워크플로는 최소 권한을 사용합니다. Issue/PR 생성은 `GITHUB_TOKEN`으로 수행합니다.
- 공개 저장소이므로 CI 아티팩트는 JSON/TXT/LOG만 업로드하고, 이메일 주소는 스크럽합니다.
- 비밀번호/2FA 자동화는 하지 않습니다. 로그인은 실제 브라우저에서 사람이 수행합니다.
- API fallback도 서비스 계정으로는 Blogger 쓰기가 불가능합니다. 사용자 OAuth 동의가 필요합니다.

## 11. 확장 지점

- **새 블록 타입**: `models.py`의 `Block` union, `render.py`의 렌더 함수, `quality.py`의 텍스트/HTML 검사, 템플릿을 함께 수정합니다.
- **새 콘텐츠 유형**: `ContentType` enum과 `config/astrion.yaml`의 `quality.min_words`를 함께 수정합니다.
- **새 주제 영역**: `Category` enum에 추가합니다. 단, 9개 고정 원칙을 바꾸는 결정이므로 문서와 테스트를 함께 갱신합니다.
- **이미지 지원**: 현재 블록에 없습니다. 추가 시 `alt` 필수, 외부 스크립트 금지, Blogger 이미지 업로드 경로를 별도로 설계해야 합니다.
- **동기화**: `astrion sync`는 아직 없습니다. Blogger의 실제 게시물 목록과 로컬 상태를 대조하는 명령을 추가할 수 있습니다.

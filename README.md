# Astrion

Astrion은 수학·과학 주제를 **사람이 선별·검수한 콘텐츠 모듈**로 작성하고, 결정론적 파이프라인으로 Google Blogger에 발행하는 프로젝트입니다. OpenAI, Anthropic 등 외부 AI API를 콘텐츠 생성이나 발행 판단에 사용하지 않습니다.

현재 발행 경로는 **저장된 Google 브라우저 세션으로 Blogger 웹 에디터를 직접 조작하는 방식**(Playwright)입니다. Blogger API OAuth 앱이 Google의 "Testing" 상태에 머물러 refresh token이 7일마다 만료되는 문제를 피하기 위해서입니다. API 클라이언트는 명시적 fallback(`blogger.backend: api`)으로 남아 있습니다.

운영 발행은 `blogger.is_draft: true`(초안 모드)를 기본값으로 두고, 웹 발행 경로를 검증한 뒤 라이브로 전환합니다.

## 핵심 원칙

- **No AI API**: 주제 선택, 본문, 품질 판정, 발행은 모두 규칙과 사람의 검수로 결정됩니다.
- **결정론적**: 같은 입력은 같은 HTML, 같은 품질 점수, 같은 큐 페이로드를 만듭니다.
- **품질 우선**: 하루 2개. 품질 기준을 통과하지 못하면 발행하지 않습니다.
- **중복 금지**: 제목이 달라도 핵심 질문·설명 대상·중심 원리가 같으면 중복으로 봅니다.
- **실패를 숨기지 않음**: 실패한 페이로드와 원본 HTML을 `data/archive/failed/`에 보존하고 GitHub Issue로 알립니다.
- **영어 전용**: 본문, 제목, 메타 설명, 캡션에 한국어(한글)를 허용하지 않습니다.

## 구조

```text
content/                 주제 모듈(YAML)
  mathematics/
  physics/
astrion/                 Python 패키지
  models.py              스키마와 검증
  loader.py              YAML 로딩
  validate.py            교차 모듈 검증
  duplicate.py           편집 서명 기반 중복 탐지
  quality.py             100점 결정론적 품질 게이트
  render.py              Blogger HTML 렌더러
  planner.py             날짜별 주제 선택과 슬롯 배정
  builder.py             큐 페이로드 생성
  queue.py               큐 계약/검증/아카이브
  web_publisher.py       웹 에디터 발행 브리지(Node 호출)
  blogger.py             Blogger API v3 클라이언트(선택 fallback)
  publisher.py           멱등 발행과 재시도
  auth.py                (선택) API용 OAuth 도우미
  cli.py                 명령줄 인터페이스
web/                     Playwright 웹 발행 엔진
  selectors.json         Blogger 에디터 선택자 맵(자동 갱신 대상)
  publishBlogger.mjs     초안 저장/라이브 발행
  selectorHealth.mjs     선택자 검사 및 추론
  sessionCheck.mjs       세션/에디터 준비 상태 확인
  login.mjs              대화형 로그인 → storage state 저장
  findPost.mjs           제목으로 게시물 조회(중복 방지)
config/
  astrion.yaml           사이트/품질/발행 설정
  blogger_contract.json  Blogger discovery 계약 스냅샷
data/
  state.json             발행/대기/실패 상태 원장(커밋 대상)
  queue/                 발행 대기 페이로드(커밋 대상)
  archive/               성공/실패 아카이브(커밋 대상)
docs/                    설계 및 운영 문서
.github/workflows/       CI, 빌드, 발행, 에디터 건강검진
tests/                   pytest 테스트
```

## 빠른 시작

Python 3.11 이상과 Node.js 20 이상이 필요합니다.

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
# source .venv/bin/activate

pip install -r requirements-dev.txt
npm install
python -m pytest
python -m astrion validate
python -m astrion plan
python -m astrion build --dry-run
```

첫 발행 전에 브라우저 세션을 한 번 저장합니다.

```bash
npm run login                # 브라우저에서 Google 로그인
npm run session:check        # 에디터가 열리는지 확인
npm run selectors:check      # 에디터 선택자 상태 확인
```

`npm run login`이 출력하는 Base64 값을 `BLOGGER_STORAGE_STATE` Secret에 넣으면 GitHub Actions가 같은 세션을 사용합니다.

패키지를 설치하면 `astrion` 명령을 직접 쓸 수 있습니다.

```bash
pip install -e .
astrion validate
```

## 주요 명령

| 명령 | 설명 |
| --- | --- |
| `python -m astrion validate` | 모든 주제 모듈과 교차 중복을 검증합니다. |
| `python -m astrion list` | 주제 목록을 상태/카테고리와 함께 표시합니다. |
| `python -m astrion check-duplicates` | 주제 쌍별 중복 후보를 보고합니다. |
| `python -m astrion render <topic_id>` | 한 주제를 HTML 미리보기로 렌더링합니다. |
| `python -m astrion plan` | 오늘 올릴 주제와 슬롯을 미리 계산합니다(쓰기 없음). |
| `python -m astrion build` | 큐 페이로드와 미리보기를 생성하고 상태를 예약합니다. |
| `python -m astrion publish --dry-run` | 세션/자격 증명 없이 큐 검증과 발행 시뮬레이션을 수행합니다. |
| `python -m astrion publish --due` | 발행 시각이 지난 큐 항목을 설정된 백엔드(기본: 웹 에디터)로 발행합니다. |
| `python -m astrion healthcheck` | 저장된 세션, 에디터, 블로그 접근을 확인합니다. |
| `python -m astrion session` | 세션/에디터 상태를 확인합니다(0=정상, 2=에디터 변경, 3=세션 만료). |
| `python -m astrion login` | `npm run login`을 실행해 브라우저 세션을 저장합니다. |
| `python -m astrion contract --check` | (API fallback) 커밋된 Blogger 계약 스냅샷과 현재 API를 비교합니다. |
| `python -m astrion contract --refresh` | (API fallback) 계약 스냅샷을 갱신합니다. |
| `python -m astrion auth` | (API fallback) OAuth refresh token을 발급합니다. |
| `python -m astrion status` | 발행/대기/실패/큐 상태를 요약합니다. |

## GitHub Actions와 Blogger

발행은 Google Apps Script가 아니라 **GitHub Actions → Playwright → Blogger 웹 에디터** 경로로 이루어집니다. Python 파이프라인은 큐 검증과 상태 관리를 맡고, `web/*.mjs`가 실제 편집기를 조작합니다.

필요한 GitHub Secrets:

```text
BLOGGER_BLOG_ID
BLOGGER_STORAGE_STATE        # npm run login이 출력한 Base64 세션
```

선택 사항(API fallback을 쓸 때만):

```text
BLOGGER_CLIENT_ID
BLOGGER_CLIENT_SECRET
BLOGGER_REFRESH_TOKEN
```

워크플로:

- `ci.yml`: Python 테스트, Node 문법 검사, 콘텐츠 검증, 당일 발행 계획 확인
- `build.yml`: 매일 00:10 America/New_York에 다음 슬롯 큐 생성 후 `data/state.json`, `data/queue` 커밋
- `publish.yml`: 매시간 실행, Playwright/Chromium 설치, 세션 healthcheck, 최대 1개 발행, 상태/아카이브 커밋, 실패 시 Issue 생성
- `web-health.yml`: 매일 09:00 UTC에 에디터 선택자를 검사하고, 깨진 항목은 추론해 PR 생성. 세션이 만료되면 Issue 생성
- `blogger-contract.yml`: (API fallback) 매주 discovery 계약을 갱신하고 변경 시 PR 생성

자세한 웹 발행 구조와 복구 절차는 [docs/07-web-publishing.md](docs/07-web-publishing.md)를 참고하십시오.

## 안전 기본값

- `config/astrion.yaml`의 `blogger.is_draft: true`
- 하루 발행 수 `site.posts_per_day: 2`
- 발행 슬롯 `08:30`, `15:30` America/New_York
- 한 실행 최대 1개(`blogger.max_per_run: 1`)
- 품질 85점 미만 PUBLISH 불가
- 검수 상태가 `approved`가 아니면 BLOCK
- 중복 편집 서명이 감지되면 BLOCK
- 초안 저장 중 "Save draft" 컨트롤을 찾지 못하면 Publish를 누르지 않고 실패
- 에디터 선택자가 깨지면 발행을 중단하고 PR/Issue로 알림

## 알려진 제한

- **웹 에디터는 공식 API가 아닙니다.** Blogger UI가 바뀌면 선택자가 깨질 수 있습니다. `web/selectors.json`과 일일 `web-health.yml`이 이를 감지·추론하지만, 100% 자동 복구를 보장하지는 않습니다.
- **세션은 Google 정책에 따라 만료될 수 있습니다.** `BLOGGER_STORAGE_STATE`가 만료되면 `npm run login`으로 다시 저장해야 합니다. 비밀번호/2FA 자동화는 하지 않습니다.
- **Blogger는 게시물의 검색 설명(meta description)을 API로 설정하지 못합니다.** `meta_description`은 큐와 품질 검사에는 쓰이지만 Blogger에는 전송되지 않습니다. 테마 또는 편집기에서 별도로 설정해야 합니다.
- 이미지 업로드/배치 블록은 아직 없습니다. 현재 콘텐츠 블록은 문단, 수식, 표, 목록, 콜아웃, 예제입니다.
- `blogger.is_draft: true`에서 발행한 글은 Blogger에 초안으로 남습니다. 자동으로 공개되지 않으므로 검증 기간에는 소량만 사용하십시오.
- `related` 링크는 대상 주제가 이미 발행되어 URL이 상태에 기록된 경우에만 렌더링됩니다.
- API fallback(`blogger.backend: api`)은 OAuth 앱이 Testing 상태이면 refresh token이 7일 후 만료됩니다.

## 문서

- [편집 표준](docs/00-editorial-standard.md)
- [아키텍처](docs/01-architecture.md)
- [콘텐츠 모델](docs/02-content-model.md)
- [발행](docs/03-publishing.md)
- [품질 게이트](docs/04-quality-gates.md)
- [운영](docs/05-operations.md)
- [로드맵](docs/06-roadmap.md)
- [웹 에디터 발행](docs/07-web-publishing.md)

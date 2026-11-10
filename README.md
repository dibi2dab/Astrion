# Astrion

Astrion은 수학·과학 주제를 **사람이 선별·검수한 콘텐츠 모듈**로 작성하고, 결정론적 파이프라인으로 Google Blogger에 발행하는 프로젝트입니다. OpenAI, Anthropic 등 외부 AI API를 콘텐츠 생성이나 발행 판단에 사용하지 않습니다.

현재 단계는 **설계 문서 + 최소 CLI**입니다. 실제 운영 발행은 `blogger.is_draft: true`(초안 모드)를 기본값으로 두고, Blogger API 연동을 검증한 뒤 라이브로 전환합니다.

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
  blogger.py             Blogger API v3 클라이언트
  publisher.py           멱등 발행과 재시도
  auth.py                최초 1회 OAuth 도우미
  cli.py                 명령줄 인터페이스
config/
  astrion.yaml           사이트/품질/발행 설정
  blogger_contract.json  Blogger discovery 계약 스냅샷
data/
  state.json             발행/대기/실패 상태 원장(커밋 대상)
  queue/                 발행 대기 페이로드(커밋 대상)
  archive/               성공/실패 아카이브(커밋 대상)
docs/                    설계 및 운영 문서
.github/workflows/       CI, 빌드, 발행, 계약 갱신
tests/                   pytest 테스트
```

## 빠른 시작

Python 3.11 이상이 필요합니다.

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
# source .venv/bin/activate

pip install -r requirements-dev.txt
python -m pytest
python -m astrion validate
python -m astrion plan
python -m astrion build --dry-run
```

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
| `python -m astrion publish --dry-run` | 자격 증명 없이 큐 검증과 발행 시뮬레이션을 수행합니다. |
| `python -m astrion publish --due` | 발행 시각이 지난 큐 항목을 Blogger API로 발행합니다. |
| `python -m astrion healthcheck` | 자격 증명, 블로그 접근, API 계약을 확인합니다. |
| `python -m astrion contract --check` | 커밋된 Blogger 계약 스냅샷과 현재 API를 비교합니다. |
| `python -m astrion contract --refresh` | 계약 스냅샷을 갱신합니다. |
| `python -m astrion auth` | 최초 1회 OAuth refresh token을 발급합니다. |
| `python -m astrion status` | 발행/대기/실패/큐 상태를 요약합니다. |

## GitHub Actions와 Blogger

발행은 Google Apps Script가 아니라 **GitHub Actions → Blogger API v3 직접 호출**로 이루어집니다.

필요한 GitHub Secrets:

```text
BLOGGER_BLOG_ID
BLOGGER_CLIENT_ID
BLOGGER_CLIENT_SECRET
BLOGGER_REFRESH_TOKEN
```

워크플로:

- `ci.yml`: 테스트, 콘텐츠 검증, 당일 발행 계획 확인
- `build.yml`: 매일 00:10 America/New_York에 다음 슬롯 큐 생성 후 `data/state.json`, `data/queue` 커밋
- `publish.yml`: 매시간 실행, 발행 시각이 지난 항목을 최대 1개 발행, 상태/아카이브 커밋, 실패 시 Issue 생성
- `blogger-contract.yml`: 매주 Blogger discovery 계약을 갱신하고 변경 시 PR 생성

자세한 OAuth 설정과 운영 절차는 [docs/03-publishing.md](docs/03-publishing.md)를 참고하십시오.

## 안전 기본값

- `config/astrion.yaml`의 `blogger.is_draft: true`
- 하루 발행 수 `site.posts_per_day: 2`
- 발행 슬롯 `08:30`, `15:30` America/New_York
- 한 실행 최대 1개(`blogger.max_per_run: 1`)
- 품질 85점 미만 PUBLISH 불가
- 검수 상태가 `approved`가 아니면 BLOCK
- 중복 편집 서명이 감지되면 BLOCK
- Blogger API 계약이 커밋된 스냅샷과 다르면 발행 중단

## 알려진 제한

- **Blogger API v3는 게시물의 검색 설명(meta description)을 직접 설정하지 못합니다.** `meta_description`은 큐와 품질 검사에는 쓰이지만 Blogger에는 전송되지 않습니다. 테마 또는 Blogger 편집기에서 별도로 설정해야 합니다.
- 이미지 업로드/배치 블록은 아직 없습니다. 현재 콘텐츠 블록은 문단, 수식, 표, 목록, 콜아웃, 예제입니다.
- `blogger.is_draft: true`에서 발행한 글은 Blogger에 초안으로 남습니다. 자동으로 공개되지 않으므로 검증 기간에는 소량만 사용하십시오.
- Blogger API 변경 시 코드를 스스로 재작성하지는 않습니다. 계약 스냅샷, 발행 전 healthcheck, 실패 Issue, 주간 갱신 PR로 **변경을 감지하고 발행을 안전하게 중단**합니다.
- `related` 링크는 대상 주제가 이미 발행되어 URL이 상태에 기록된 경우에만 렌더링됩니다.

## 문서

- [편집 표준](docs/00-editorial-standard.md)
- [아키텍처](docs/01-architecture.md)
- [콘텐츠 모델](docs/02-content-model.md)
- [발행](docs/03-publishing.md)
- [품질 게이트](docs/04-quality-gates.md)
- [운영](docs/05-operations.md)
- [로드맵](docs/06-roadmap.md)

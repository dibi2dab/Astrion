# 발행

> **기본 백엔드는 웹 에디터입니다.** Astrion은 저장된 Google 브라우저 세션으로 Blogger 편집기를 조작해 글을 씁니다. 전체 구조와 복구 절차는 [웹 에디터 발행](07-web-publishing.md)을 먼저 읽으십시오.
>
> 이 문서의 OAuth/API 절차는 `blogger.backend: api`로 명시했을 때만 사용하는 **fallback**입니다.

Astrion은 Google Apps Script를 사용하지 않습니다. GitHub Actions가 Playwright로 **Blogger 웹 에디터**를 조작해 게시물을 만들고, 선택적으로 Blogger API v3를 직접 호출할 수 있습니다. 이 문서는 API fallback의 OAuth 설정과 공통 발행/실패 처리 절차를 다룹니다.

## 1. 발행 흐름 요약

```text
build.yml (매일)
  → validate --strict
  → build (큐 페이로드 + 상태 예약)
  → data/state.json, data/queue, data/archive 커밋

publish.yml (매시간)
  → healthcheck (웹: 세션+에디터 / API: 계약)
  → publish --due
      → 큐 재검증
      → 중복 방어
      → 웹 에디터 저장/발행 (또는 posts.insert)
      → state 기록 + 아카이브
  → 상태/큐/아카이브 커밋
  → 실패 시 Issue 생성
```

## 2. 사전 준비

> 기본 웹 백엔드는 `npm install`, `npx playwright install chromium`, `npm run login`만 필요합니다. 아래 2~6단계는 API fallback 전용입니다.

1. Google 계정으로 Blogger 블로그를 만듭니다.
2. (API fallback) Google Cloud 프로젝트를 만들고 **Blogger API v3**를 사용 설정합니다.
3. (API fallback) OAuth 동의 화면을 구성합니다.
4. (API fallback) OAuth 클라이언트(ID/Secret)를 만듭니다.
5. (API fallback) 로컬에서 refresh token을 발급합니다.
6. GitHub Secrets에 필요한 값을 등록합니다. 웹: `BLOGGER_BLOG_ID`, `BLOGGER_STORAGE_STATE`. API: `BLOGGER_CLIENT_ID`, `BLOGGER_CLIENT_SECRET`, `BLOGGER_REFRESH_TOKEN`.
7. `healthcheck`와 초안 발행으로 검증합니다.
8. 검증이 끝난 뒤에만 `is_draft: false`로 전환합니다.

## 3. Google Cloud 설정

### 3.1 프로젝트와 API

1. [Google Cloud Console](https://console.cloud.google.com/)에서 프로젝트를 만듭니다.
2. **APIs & Services → Library**에서 `Blogger API v3`를 검색해 **Enable**합니다.
3. **APIs & Services → OAuth consent screen**으로 이동합니다.
   - User type: 조직 계정이면 **Internal**, 개인이면 **External**을 선택합니다.
   - App name, 지원 이메일, 개발자 연락처를 입력합니다.
   - Scope에 `https://www.googleapis.com/auth/blogger`를 추가합니다.
   - External + Testing 상태라면 **Test users**에 발행에 사용할 Google 계정을 추가합니다.

> **중요**: External 앱이 **Testing** 상태이면 Google이 발급한 refresh token이 **7일 후 만료**됩니다. 장기 운영에는 다음 중 하나를 선택하십시오.
>
> - 앱을 **Publishing** 상태로 전환하고 필요한 검증을 완료합니다.
> - Google Workspace 조직 계정에서 **Internal** 앱으로 만듭니다(조직 내부 사용자는 7일 제한이 적용되지 않습니다).
>
> 7일 만료를 방치하면 `publish.yml`이 `invalid_grant`로 실패하고 Issue가 열립니다. 이는 조용한 실패가 아니라 의도된 경고입니다.

### 3.2 OAuth 클라이언트

1. **APIs & Services → Credentials → Create credentials → OAuth client ID**를 선택합니다.
2. Application type은 **Web application**입니다.
3. **Authorized redirect URIs**에 다음을 추가합니다.

```text
http://127.0.0.1:8765/oauth2callback
```

`astrion auth --port <번호>`를 쓰면 해당 포트의 `http://127.0.0.1:<번호>/oauth2callback`도 등록해야 합니다.

4. 생성된 **Client ID**와 **Client Secret**을 기록합니다.

## 4. Refresh token 발급

로컬 프로젝트 루트에서 실행합니다. 이 명령은 CI가 아니라 **본인 컴퓨터에서 한 번만** 실행합니다.

```bash
# 환경 변수로 전달
export BLOGGER_CLIENT_ID="....apps.googleusercontent.com"
export BLOGGER_CLIENT_SECRET="...."

python -m astrion auth
```

Windows PowerShell:

```powershell
$env:BLOGGER_CLIENT_ID = "....apps.googleusercontent.com"
$env:BLOGGER_CLIENT_SECRET = "...."
.venv\Scripts\python -m astrion auth
```

동작:

1. 로컬 HTTP 서버가 `127.0.0.1:8765`에서 대기합니다.
2. 브라우저가 열리고 Google 동의 화면이 표시됩니다.
3. 승인하면 `http://127.0.0.1:8765/oauth2callback`로 돌아옵니다.
4. 터미널에 `BLOGGER_REFRESH_TOKEN=...`이 출력됩니다.

브라우저를 자동으로 열지 않으려면:

```bash
python -m astrion auth --no-browser
```

출력된 URL을 직접 열면 됩니다.

> Refresh token은 비밀번호와 같습니다. 채팅, 이슈, 커밋, 로그에 남기지 마십시오.

## 5. GitHub Secrets

저장소 **Settings → Secrets and variables → Actions → New repository secret**에 다음을 등록합니다.

| Secret | 값 |
| --- | --- |
| `BLOGGER_BLOG_ID` | Blogger 블로그 ID |
| `BLOGGER_CLIENT_ID` | OAuth Client ID |
| `BLOGGER_CLIENT_SECRET` | OAuth Client Secret |
| `BLOGGER_REFRESH_TOKEN` | `astrion auth`가 출력한 refresh token |

### 블로그 ID 찾기

- Blogger 대시보드에서 블로그를 열면 URL에 `blogID=...`가 표시됩니다.
- 또는 Google APIs Explorer에서 `blogger.blogs.getByUrl`을 호출해 `id`를 확인합니다.
- `BLOGGER_BLOG_ID`는 숫자 문자열인 경우가 많지만, API가 반환한 값을 그대로 사용하십시오.

## 6. 최초 검증

### 6.1 Healthcheck

로컬에서 자격 증명을 환경 변수로 넣고 실행합니다.

```bash
export BLOGGER_BLOG_ID="..."
export BLOGGER_CLIENT_ID="..."
export BLOGGER_CLIENT_SECRET="..."
export BLOGGER_REFRESH_TOKEN="..."

python -m astrion healthcheck
python -m astrion healthcheck --strict-contract --json
```

정상 출력 예:

```text
OK blog 1234567890 Astrion (https://astrion.blogspot.com/)
OK Blogger API reachable; discovery revision 20261108
contract: unchanged
```

- `contract: changed` 또는 `missing`이면 `config/blogger_contract.json`과 현재 API가 다르다는 뜻입니다.
- `--strict-contract`는 이 경우 종료 코드 1을 반환합니다. `publish.yml`이 이 모드를 사용합니다.

### 6.2 초안 발행

`config/astrion.yaml`의 기본값은 `blogger.is_draft: true`입니다.

```yaml
blogger:
  is_draft: true
  max_secondary_labels: 4
  max_per_run: 1
  max_attempts: 3
  timeout_seconds: 30
```

초안 모드에서는 웹 백엔드가 **Save draft**만 누르고 Publish는 누르지 않습니다. API fallback은 `posts.insert`에 `isDraft=true`를 전달합니다. 어느 쪽이든 자동으로 공개되지 않습니다.

절차:

```bash
# 1. 검증
python -m astrion validate --strict

# 2. 큐 생성 (오늘 또는 특정 날짜)
python -m astrion build --date 2026-11-10

# 3. 자격 증명 없이 페이로드 검증
python -m astrion publish --dry-run --due

# 4. 실제 초안 1개 발행
python -m astrion publish --due --max 1
```

또는 GitHub에서 `publish.yml`을 수동 실행(`workflow_dispatch`)하고 `queue_id`를 지정합니다.

확인할 것:

- Blogger **Drafts**에 게시물이 생겼는가?
- 제목, 라벨, 본문 HTML, 표, 수식이 정상인가?
- `data/archive/published/YYYY-MM/`에 JSON과 HTML이 남았는가?
- `data/state.json`의 `published`에 `blogger_post_id`와 `url`이 기록되었는가?

### 6.3 라이브 전환

초안 검증이 끝난 뒤에만 다음을 수행합니다.

1. `config/astrion.yaml`에서 `is_draft: false`로 변경합니다.
2. 변경을 커밋합니다.
3. `publish.yml`을 수동 실행해 실제 공개 발행을 1회 테스트합니다.
4. 게시물 URL, 라벨, 수식, 모바일 레이아웃을 확인합니다.
5. 문제가 있으면 즉시 `is_draft: true`로 되돌립니다.

> `is_draft: true` 상태로 오래 운영하지 마십시오. 초안은 검색에 노출되지 않고, 독자는 글을 볼 수 없습니다.

## 7. 워크플로 상세

### 7.1 `build.yml`

- 트리거: 매일 `05:10 UTC`, 수동 실행.
- `05:10 UTC`는 `00:10 EST` 또는 `01:10 EDT`입니다. 두 경우 모두 08:30 슬롯 이전입니다.
- 하는 일:
  1. `validate --strict`
  2. `build` (날짜 지정 가능)
  3. `status` 출력
  4. `data/state.json`, `data/queue`, `data/archive` 커밋
- 수동 입력: `date` (`YYYY-MM-DD`, 비우면 오늘).

### 7.2 `publish.yml`

- 트리거: 매시간 `7 * * * *`, 수동 실행.
- 하는 일:
  1. Node/Playwright Chromium 설치
  2. `healthcheck --json` (웹: 세션/에디터, API: 계약)
  3. `publish --due` (또는 지정한 `queue_id`)
  4. 상태/큐/아카이브 커밋
  5. `dist/`의 JSON/TXT/LOG 진단만 업로드 (공개 저장소이므로 스크린샷/HTML 제외)
  6. 실패 시 Issue 생성
- 수동 입력: `queue_id`, `dry_run`(불리언).
- `concurrency.group: astrion-state`로 `build.yml`과 동시에 실행되지 않습니다.

### 7.3 `web-health.yml`

- 트리거: 매일 `09:00 UTC`, 수동 실행, `publish.yml` 실패 후.
- `npm run selectors:check`로 모든 필수 선택자를 검사합니다.
- 종료 코드 2(선택자 깨짐)이면 `npm run selectors:fix`로 추론하고 `web/selectors.json` 변경 시 PR을 만듭니다.
- 종료 코드 3(세션 만료)이면 복구 절차가 담긴 Issue를 만듭니다.
- 업로드 아티팩트는 JSON/TXT/LOG로 제한합니다.

### 7.4 `blogger-contract.yml`

- 트리거: 매주 월요일 `06:17 UTC`, 수동 실행.
- (API fallback 전용) `contract --refresh` 실행.
- SHA-256이 이전과 같으면 파일을 건드리지 않고 종료합니다.
- 다르면 `chore/blogger-contract-<run_id>` 브랜치를 만들어 PR을 생성합니다.
- 필수 메서드/파라미터가 사라졌으면 실패 Issue를 생성합니다.

### 7.5 `ci.yml`

- push/PR/수동 실행.
- `pytest`, `npm run check`, `validate --strict`, `build --dry-run`.
- 쓰기 권한이 없습니다.

## 8. 발행 시각과 DST

- 큐의 `publish_after`는 오프셋이 포함된 절대 시각입니다. 예: `2026-11-10T08:30:00-05:00`.
- `publish.yml`은 매시간 실행되지만, `publish_after <= now`인 항목만 발행합니다.
- 슬롯 시각이 이미 지난 경우(예: 수동 빌드가 늦게 실행됨) `publish_after`는 현재 시각 + 3분(두 번째 항목은 +5분)으로 올라갑니다. 과거 시각으로 발행하지 않습니다.
- DST가 바뀌어도 크론을 두 벌 관리할 필요가 없습니다.

## 9. 멱등성과 재시도

### 9.1 중복 발행 방지

게시물 HTML에는 두 개의 마커가 들어갑니다.

```html
<div class="astrion-post"
     data-astrion-topic="physics.rayleigh-scattering-sky"
     data-astrion-queue="2026-11-10-s2-why-the-sky-is-blue-...">
```

- 발행 전 `state.find_published(topic_id=...)`로 이미 발행된 주제인지 확인합니다.
- 이전 시도가 있고(`attempts > 0`) 아직 성공 기록이 없으면 이전 부분 발행을 찾습니다.
  - **웹 백엔드**: `findPost.mjs`가 게시물 목록을 **제목**으로 조회합니다. 목록 화면은 HTML 마커 검색을 지원하지 않기 때문입니다.
  - **API fallback**: `find_recent_by_marker(topic_id)`가 **live와 draft** 게시물 본문에서 `data-astrion-topic` 마커를 검색합니다.
- 찾으면 새로 만들지 않고 기존 게시물을 상태에 기록하고 아카이브합니다.
- `in_flight` 상태로 남은 항목은 다음 실행에서 먼저 조정(reconcile)합니다.

### 9.2 재시도

- 재시도 가능한 오류: HTTP 429, 500, 502, 503, 504, `rateLimitExceeded`, `userRateLimitExceeded`, `backendError`, `internalError`, 네트워크 오류.
- `max_attempts`(기본 3)까지 재시도합니다.
- 다음 시도는 `15분 × 시도 횟수` 뒤로 예약됩니다(`next_attempt_at`).
- 재시도 대기 중인 항목은 `skipped`로 보고되고 큐에 남습니다.
- `max_attempts`를 초과하면 실패 아카이브로 이동하고 상태에 `failed`로 기록합니다.

### 9.3 즉시 중단하는 오류

- `BloggerAuthError` (401/403, 토큰 갱신 실패): 발행 루프를 즉시 중단합니다. 페이로드는 큐에 남습니다.
- `BloggerDriftError` (응답 형태 변경, 계약 위반): 즉시 중단합니다. 잘못된 응답으로 게시물을 만들지 않습니다.
- 두 경우 모두 워크플로가 실패하고 Issue가 열립니다.

## 10. 실패 보존과 복구

실패 시:

```text
data/archive/failed/YYYY-MM/<queue_id>.json
data/archive/failed/YYYY-MM/<queue_id>.html
data/archive/failed/YYYY-MM/<queue_id>.error.txt
```

- `state.json`의 `failed`에 `last_error`, `failed_at`이 기록됩니다.
- 큐에서는 제거됩니다.
- 원본 HTML과 페이로드가 남으므로 원인 분석과 수동 재발행이 가능합니다.

복구 방법:

1. `queue_id`와 실패 원인을 확인합니다.
2. 원인이 콘텐츠/검증이면 모듈을 수정하고 다시 `build`합니다.
3. 원인이 일시적 API 오류인데 `max_attempts`를 초과했다면, 실패 아카이브의 페이로드를 검토한 뒤 새 큐 항목으로 다시 빌드하는 것을 권장합니다.
4. 상태를 직접 수정하는 것은 최후의 수단입니다. 수정 전에 `data/state.json`을 백업하십시오.

## 11. 알려진 제한

### 11.1 메타 설명

Blogger API v3의 게시물 리소스에는 검색 설명(meta description)을 설정하는 필드가 없습니다.

- `meta_description`은 큐 페이로드, 품질 검사, SEO 점수에 사용됩니다.
- Blogger에는 전송되지 않습니다.
- 검색 결과 설명을 제어하려면 Blogger 테마(HTML 편집)에서 조건부 메타 태그를 추가하거나, 게시물 편집기에서 별도로 설정해야 합니다.

### 11.2 관련 글 링크

- `related`는 대상 주제가 이미 발행되어 `state.json`에 URL이 있을 때만 렌더링됩니다.
- 발행 전 빌드 시점에는 링크가 없을 수 있습니다.
- 나중에 대상이 발행되어도 기존 게시물이 자동으로 수정되지는 않습니다. 필요하면 수동으로 다시 발행하거나 `posts.patch`를 사용하는 별도 작업이 필요합니다.

### 11.3 이미지

- 현재 블록 타입에 이미지가 없습니다.
- 이미지를 추가하려면 Blogger 이미지 업로드 경로와 `alt` 검증을 먼저 설계해야 합니다.

### 11.4 초안 모드

- `is_draft: true`에서 만든 글은 Blogger 초안입니다.
- 검색 노출, RSS, 독자 접근이 없습니다.
- 검증 기간에만 사용하고, 검증 후 라이브로 전환하십시오.

## 12. 보안 체크리스트

- [ ] Refresh token을 저장소에 커밋하지 않았는가?
- [ ] Secrets는 GitHub Environments/Repository Secrets에만 있는가?
- [ ] OAuth 동의 화면의 Test users를 최소화했는가?
- [ ] 7일 만료 가능성을 인지하고 있는가?
- [ ] 워크플로 권한이 최소인가?
- [ ] `data/state.json`과 큐 파일이 커밋 대상인가?
- [ ] 실패 Issue가 활성화되어 있는가?
- [ ] `is_draft` 기본값이 안전한가?

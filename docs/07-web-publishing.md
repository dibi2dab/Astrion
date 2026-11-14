# 웹 에디터 발행 (기본 백엔드)

Astrion은 Blogger API v3 대신 **저장된 Google 브라우저 세션으로 Blogger 웹 에디터를 직접 조작**해 글을 발행합니다. 이 문서는 그 구조, 최초 설정, 선택자 유지보수, 실패 복구 절차를 설명합니다.

## 왜 웹 에디터인가

기존 경로는 OAuth 2.0 refresh token으로 `posts.insert`를 호출했습니다. 문제는 Google Cloud OAuth 앱이 **External + Testing** 상태에 머무는 동안 refresh token이 **7일 후 만료**된다는 점입니다. 장기 무인 운영에는 맞지 않습니다.

그래서 `MVP-Blog`가 Tistory에서 쓰는 방식과 동일하게, **사람이 한 번 로그인한 브라우저 세션**을 저장하고 Playwright로 편집기를 조작합니다.

- 비밀번호를 저장하지 않습니다.
- 2FA/디바이스 인증을 자동화하지 않습니다.
- 로그인은 실제 브라우저 창에서 사람이 직접 수행합니다.
- 세션이 만료되면 명확한 오류 코드와 Issue로 알립니다.

API 클라이언트(`astrion/blogger.py`)는 삭제하지 않았습니다. `blogger.backend: api`로 명시하면 기존 OAuth 경로를 fallback으로 사용할 수 있습니다.

## 구조

```text
GitHub Actions (publish.yml)
  → python -m astrion healthcheck         # 세션/에디터 확인
  → python -m astrion publish --due
      → astrion/publisher.py              # 큐/상태/멱등성/중복 방어
      → astrion/web_publisher.py          # Node 브리지
          → node web/publishBlogger.mjs   # Playwright 자동화
              → Blogger 웹 에디터
```

| 파일 | 역할 |
| --- | --- |
| `astrion/web_publisher.py` | Base64 세션 복원, Node 실행, sentinel 파싱, 오류 매핑 |
| `astrion/publisher.py` | 발행 루프, 재시도, in-flight 재조정, 중복 방어 |
| `web/selectors.json` | 에디터 선택자 맵(`primary` + 후보 목록) |
| `web/publishBlogger.mjs` | 제목/본문/라벨 입력, 초안 저장 또는 라이브 발행 |
| `web/sessionCheck.mjs` | 세션과 필수 컨트롤 확인 |
| `web/selectorHealth.mjs` | 선택자 검사(`check`) 및 추론/패치(`fix`) |
| `web/login.mjs` | 대화형 로그인 후 storage state 저장 |
| `web/findPost.mjs` | 제목으로 게시물 ID/상태 조회(중복 방지) |
| `web/lib.mjs` | 공통 선택자/세션/브라우저/아티팩트 헬퍼 |

## 최초 설정

### 1. 의존성 설치

```bash
npm install
npx playwright install chromium
```

CI(`ubuntu-latest`)에서는 `npx playwright install --with-deps chromium`을 사용합니다.

### 2. 브라우저 세션 저장

```bash
npm run login
```

1. 실시간 Chrome 창이 열립니다(`ASTRION_BROWSER_CHANNEL=bundled`로 Chromium 강제 가능).
2. 블로그 소유 Google 계정으로 로그인합니다.
3. Blogger 게시물 목록이 보이면 자동으로 세션이 저장됩니다.
4. Google 인증 쿠키가 없으면 저장을 거부하고 다시 로그인하도록 안내합니다.
5. 터미널에 `BLOGGER_STORAGE_STATE (base64): ...`가 출력됩니다.

저장 위치는 `.state/blogger_storage_state.json`이며 `.gitignore`에 포함됩니다. 절대 커밋하지 마십시오.

Python CLI로도 실행할 수 있습니다.

```bash
python -m astrion login
```

### 3. GitHub Secret 설정

저장소 → Settings → Secrets and variables → Actions에서 다음을 추가합니다.

| Secret | 필수 | 설명 |
| --- | --- | --- |
| `BLOGGER_BLOG_ID` | 예 | `7103426249941439830` |
| `BLOGGER_STORAGE_STATE` | 예 | `npm run login`이 출력한 Base64 값 |
| `BLOGGER_CLIENT_ID` | 아니오 | API fallback 전용 |
| `BLOGGER_CLIENT_SECRET` | 아니오 | API fallback 전용 |
| `BLOGGER_REFRESH_TOKEN` | 아니오 | API fallback 전용 |

`BLOGGER_BLOG_URL`은 `config/astrion.yaml`에 기본값이 있습니다. 필요하면 Secret/환경변수로 덮어쓸 수 있습니다.

### 4. 검증

```bash
npm run session:check
npm run selectors:check
python -m astrion healthcheck --json
```

- `session:check`: 세션이 에디터를 열 수 있는지 확인합니다.
- `selectors:check`: 필수 컨트롤이 모두 보이는지 확인합니다.
- `healthcheck`: 큐/상태와 무관하게 백엔드 준비 상태를 확인합니다.

세션 검사는 **초안 게시물을 우선** 열어 `Save` 메뉴까지 확인합니다. 초안이 없으면 공개 게시물을 열고 `Save`/`Publish` 선택자는 `skipped`로 표시합니다(공개 게시물은 `Update`만 제공하므로 드리프트가 아님).

## 발행 흐름

1. Python이 큐 페이로드의 품질/중복/상태를 다시 검증합니다.
2. `publisher.py`가 in-flight 항목을 먼저 재조정합니다.
3. `web_publisher.py`가 `BLOGGER_STORAGE_STATE`를 `.state/`에 복원합니다.
4. Node가 Chromium을 실행하고 게시물 목록에서 **Create New Post**를 누릅니다. Blogger가 새 초안 ID를 서버에서 만들므로 URL을 미리 알 수 없습니다.
5. 생성된 초안을 다시 URL로 엽니다. 생성 직후 타이핑하면 에디터 초기화와 경쟁해 내용이 덮어써질 수 있습니다.
6. 제목을 입력하고 값을 다시 읽어 확인합니다.
7. 본문을 에디터 iframe(`iframe.editable` 안의 `body[contenteditable]`)에 주입하고 길이를 확인합니다. 이전 UI의 HTML view는 fallback으로만 사용합니다.
8. 라벨을 입력합니다. 라벨 입력창이 없으면 라벨만 건너뛰고 경고를 남깁니다.
9. 에디터가 **Changes saved** 상태로 안정될 때까지 기다립니다. 이 대기 없이 저장하면 직전(빈) 리비전이 저장될 수 있습니다.
10. `blogger.is_draft: true`이면 Publish 분할 버튼의 **More options → Save**만 누릅니다.
11. 게시물 목록에서 제목을 최대 3회 확인하고, 저장된 초안을 다시 열어 제목/본문이 실제로 남았는지 검증합니다.
12. Python이 상태 원장과 아카이브를 갱신합니다.

### 라벨 입력

현재 Blogger 라벨 필드는 칩 위젯이 아니라 **쉼표로 구분하는 textarea**입니다.

- `fill()`이나 `Enter`는 모델에 커밋되지 않습니다(`Enter`는 줄바꿈만 삽입).
- 실제 키보드 입력으로 `라벨,` 형태를 만들고 마지막에 쉼표를 붙인 뒤 blur해야 저장됩니다.
- 예: `Physics, Optics,`

### 초안 안전 장치

초안 모드에서 "Save" 컨트롤을 찾지 못하면 **Publish를 누르지 않고 실패**합니다.

- 현재 UI에서는 Publish 옆 분할 버튼의 **More options → Save**만 사용합니다.
- 직접 Save 버튼이 없으면 이 메뉴를 엽니다.
- 메뉴가 없으면 `BLOGGER_DRAFT_BUTTON_MISSING`으로 종료합니다.
- 클릭 직전에 컨트롤 라벨이 `Save`인지 확인합니다. `Publish`/`Update`가 매칭되면 클릭하지 않고 실패합니다.
- 에디터가 `Update`를 보여 주면 이미 공개된 글이므로 초안 경로로 저장하지 않습니다.

이 동작은 의도적입니다. 초안 요청이 실수로 공개 발행이 되는 것보다, 실패하고 Issue를 만드는 편이 안전합니다.

저장 후에는 "Changes saved" 표시만 믿지 않습니다. 목록 확인에 실패하거나, 다시 연 초안의 제목/본문이 비어 있으면 `BLOGGER_SAVE_UNCONFIRMED`로 실패합니다.

**초안은 반드시 초안으로 남아야 합니다.** 저장 후 게시물 목록에서 상태를 다시 읽어 `draft`가 아니면 `BLOGGER_DRAFT_BECAME_LIVE`로 실패합니다. 다시 연 에디터가 `Update`를 보여 줘도 같은 오류로 실패합니다. 이 검사는 2026-11-14에 초안 5편 중 4편이 공개된 사고의 재발을 막기 위해 추가되었습니다.

### 중복 방지

- 기본 방어는 `data/state.json`의 멱등성 원장입니다.
- 저장/발행 결과가 불확실하면(`BLOGGER_SAVE_UNCONFIRMED`) 다음 실행에서 **제목으로 게시물을 찾아** 중복 생성을 막습니다.
- 웹 게시물 목록은 HTML 마커 검색을 지원하지 않으므로 제목 기반 조회를 사용합니다. 제목이 비어 있으면 조회를 건너뜁니다.
- 저장 검증까지 통과한 경우에만 `savedBy: "post-list+verified"`로 기록합니다.

## 선택자 유지보수

Blogger 에디터 DOM은 예고 없이 바뀝니다. Astrion은 선택자를 코드에 흩어 두지 않고 `web/selectors.json`에 모읍니다.

```json
{
  "selectors": {
    "titleInput": {
      "optional": false,
      "primary": "input[aria-label=\"Title\"]",
      "candidates": ["input[aria-label*=\"title\" i]", "..."]
    }
  }
}
```

- `primary`: 먼저 시도하는 선택자
- `candidates`: fallback 목록
- `optional`: 없어도 발행 가능한 컨트롤

모든 Blogger URL에는 `?hl=en`을 붙입니다. 계정 UI 언어가 한국어여도 영어 라벨이 내려오므로 선택자가 안정적입니다. 한국어/일본어 라벨 후보는 fallback으로 함께 둡니다.

`body[contenteditable=""]`처럼 속성값이 빈 선택자는 DOM에 따라 `contenteditable="true"`로 바뀔 수 있으므로 후보 목록에 둘 다 유지합니다.

### 매일 자동 검사

`web-health.yml`이 매일 09:00 UTC에 실행됩니다.

1. 저장된 세션으로 게시물 목록을 열고, 초안이 있으면 그 초안을, 없으면 공개 게시물을 엽니다.
2. 모든 `primary` 선택자를 검사합니다. 초안이 없을 때 `Save`/`Publish`는 `skipped`로 기록합니다.
3. 깨진 항목이 있으면 `npm run selectors:fix`로 라이브 DOM에서 대체 선택자를 추론합니다.
4. `web/selectors.json`이 바뀌면 **PR**을 만듭니다.
5. 세션이 만료되었으면(종료 코드 3) **Issue**를 만듭니다.

발행 워크플로가 실패하면 `workflow_run` 트리거로 즉시 한 번 더 실행됩니다.

### 수동 복구

```bash
npm run selectors:check   # 0=정상, 2=깨짐, 3=세션 만료
npm run selectors:fix     # 추론 후 selectors.json 패치
git diff web/selectors.json
```

추론에 실패한 선택자는 파일을 바꾸지 않습니다. 이 경우 `artifacts`/`dist/web-artifacts/selector-health/report.json`의 `broken` 목록을 보고 수동으로 추가합니다.

## 오류 코드

| 코드 | 의미 | 대응 |
| --- | --- | --- |
| `BLOGGER_SESSION_REQUIRED` | 저장된 세션 파일 없음/복호화 실패 | `npm run login` 후 Secret 갱신 |
| `BLOGGER_SESSION_EXPIRED` | Google이 로그인 화면으로 리다이렉트 | `npm run login` 후 Secret 갱신 |
| `BLOGGER_SELECTOR_MISSING` | 필수 에디터 컨트롤을 찾지 못함 | `npm run selectors:fix` 또는 PR 검토 |
| `BLOGGER_DRAFT_BUTTON_MISSING` | 초안 저장 버튼을 찾지 못함 | 선택자 복구, 필요 시 수동 초안 저장 |
| `BLOGGER_DRAFT_BECAME_LIVE` | 초안 모드인데 게시물이 공개 상태로 저장됨 | 즉시 Blogger에서 **Revert to draft**, 원인 수정 후 재시도 |
| `BLOGGER_SAVE_UNCONFIRMED` | 저장/발행 후 확인 실패 | 다음 실행이 제목으로 재조정 |
| `BLOGGER_BODY_NOT_SET` | 본문 주입 실패 | 에디터 변경 여부 확인 |
| `BLOGGER_TITLE_NOT_SET` | 제목 입력 후 값 불일치 | 에디터 변경 여부 확인 |
| `BLOGGER_NAVIGATION_TIMEOUT` | 에디터 로딩/리다이렉트 실패 | 네트워크/세션 확인 |
| `BLOGGER_BAD_CONFIG` | blog id 등 설정 누락 | `config/astrion.yaml` 확인 |
| `BLOGGER_BAD_PAYLOAD` | Node에 전달한 payload 오류 | 큐 페이로드 확인 |
| `BLOGGER_NODE_ERROR` | Node 프로세스 비정상 종료 | 로그/아티팩트 확인, 재시도 가능 |

`BLOGGER_SAVE_UNCONFIRMED`, `BLOGGER_NODE_ERROR`, `BLOGGER_NODE_TIMEOUT`은 재시도 대상입니다. 재시도 전에 이전 부분 발행을 먼저 조회합니다.

## 보안

이 저장소는 공개 저장소입니다. 다음을 지킵니다.

- `.state/`, `*_storage_state.json`은 커밋하지 않습니다.
- `BLOGGER_STORAGE_STATE`는 GitHub Secret에만 둡니다.
- Node 아티팩트는 이메일 주소를 `***@***`로 스크럽합니다.
- CI는 스크린샷/HTML을 업로드하지 않고 **JSON/TXT/LOG만** 업로드합니다.
- 브라우저 세션 파일은 런타임에 임시로 복원되며 저장소에 남지 않습니다.
- 비밀번호, 2FA 코드, 백업 코드를 저장하거나 자동 입력하지 않습니다.

## 로컬 명령 요약

```bash
npm run login           # 세션 저장 + Base64 출력
npm run session:check   # 세션/에디터 확인
npm run selectors:check # 선택자 검사
npm run selectors:fix   # 선택자 추론/패치
npm run check           # 모든 .mjs 문법 검사
npm run publish -- payload.json   # 수동 발행 테스트
npm run find:post -- payload.json # 제목으로 게시물 조회
```

## API fallback으로 전환

`config/astrion.yaml`에서:

```yaml
blogger:
  backend: api
```

그리고 `BLOGGER_CLIENT_ID`, `BLOGGER_CLIENT_SECRET`, `BLOGGER_REFRESH_TOKEN`을 설정합니다. OAuth 앱이 Testing 상태라면 7일마다 `python -m astrion auth`로 토큰을 갱신해야 합니다.

## 남은 제약

- 웹 에디터 자동화는 공식 API가 아니므로 UI 변경에 취약합니다.
- 게시물 목록의 상태 텍스트가 바뀌면 `draft/live` 판별이 틀릴 수 있습니다. 상태 원장과 함께 검토하십시오.
- Google이 자동화 트래픽을 차단하면 세션 저장이 실패할 수 있습니다. 이 경우 수동 발행 또는 API fallback을 사용합니다.
- 이미지 업로드, 예약 발행, 댓글 관리는 아직 웹 엔진에 포함되어 있지 않습니다.

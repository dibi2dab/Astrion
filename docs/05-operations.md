# 운영

이 문서는 Astrion을 실제로 운영할 때 필요한 일상 점검, 장애 대응, 복구 절차를 정리합니다. 원칙은 하나입니다. **문제가 생기면 조용히 넘어가지 말고, 멈추고, 보존하고, 눈에 보이게 만든다.**

## 1. 운영 주기

| 주기 | 할 일 |
| --- | --- |
| 매일 | `build.yml` 성공 확인, 큐 상태 확인, 발행 결과 확인 |
| 매시간(자동) | `publish.yml` 실행. 실패 시 Issue 확인 |
| 매주 | 계약 갱신 PR 검토, 실패 아카이브 정리 여부 확인 |
| 매월 | 발행 통계, 카테고리 편중, 실패 추세, 토큰 만료 여부 점검 |
| 수시 | 새 콘텐츠 추가, 설정 변경, 자격 증명 교체 |

## 2. 최초 저장소 설정

프로젝트를 GitHub에 올린 뒤 한 번만 수행합니다.

```bash
git init
git add .
git commit -m "chore: initial Astrion pipeline"
git branch -M main
git remote add origin https://github.com/<owner>/<repo>.git
git push -u origin main
```

그다음:

1. **Settings → Secrets and variables → Actions**에 4개 Secret 등록
   - `BLOGGER_BLOG_ID`
   - `BLOGGER_CLIENT_ID`
   - `BLOGGER_CLIENT_SECRET`
   - `BLOGGER_REFRESH_TOKEN`
2. **Settings → Actions → General → Workflow permissions**에서 **Read and write permissions**를 선택합니다.
   - 워크플로가 `data/state.json`, 큐, 아카이브를 커밋하고 Issue/PR을 만들어야 합니다.
3. **Actions** 탭에서 워크플로가 활성화되었는지 확인합니다.
4. `ci.yml`을 수동 실행해 테스트와 검증이 통과하는지 확인합니다.
5. `publish.yml`을 `dry_run: true`로 수동 실행해 자격 증명과 healthcheck를 확인합니다.

### 브랜치 보호와 상태 커밋

`build.yml`과 `publish.yml`은 `main`에 직접 `git push`합니다. `main`에 "Require a pull request before merging"이 걸려 있으면 이 push가 실패합니다.

선택지:

- **권장**: 상태 파일 커밋을 위해 `main`의 PR 요구를 끄거나,
- `github-actions[bot]`을 bypass 목록에 추가하거나,
- 상태 전용 브랜치/PR 흐름으로 워크플로를 바꿉니다.

`ci.yml`은 PR에서 실행되므로, 콘텐츠 PR에는 CI 통과를 요구해도 됩니다. 다만 자동 상태 커밋까지 PR로 만들면 운영 부담이 커집니다.

## 3. 일상 점검

### 3.1 상태 확인

```bash
python -m astrion status
python -m astrion status --json
```

출력 예:

```text
Published: 12  Pending: 2  Failed: 0
Queue files: 2  invalid: 0

Pending:
  2026-11-11T08:30:00-05:00  s1  physics.rayleigh-scattering-sky  [queued]
  2026-11-11T15:30:00-05:00  s2  math.division-by-zero  [queued]
```

확인할 것:

- `Pending`이 2개인가? (하루 2개)
- `Failed`가 증가하지 않았는가?
- `Queue files`와 `Pending`이 일치하는가?
- `invalid`가 0인가?

### 3.2 GitHub Actions 확인

- `Build queue`: 매일 성공해야 합니다.
- `Publish due posts`: 실패 시 Issue가 열립니다.
- `CI`: PR마다 통과해야 합니다.
- `Blogger contract refresh`: 매주 결과 확인.

### 3.3 발행 결과 확인

- `data/state.json`의 `published`에 `blogger_post_id`, `url`, `blogger_status`가 있는가?
- `data/archive/published/YYYY-MM/`에 JSON/HTML이 있는가?
- Blogger에서 라벨, 수식, 표, 모바일 레이아웃이 정상인가?

## 4. 장애 대응

### 4.1 `validate --strict` 실패

증상: `build.yml`이 시작 직후 실패.

```text
[ERROR] physics.example: ...
1 error(s), 0 warning(s)
```

대응:

1. 로컬에서 `python -m astrion validate`를 실행해 전체 오류를 봅니다.
2. 오류 코드별로 수정합니다.
   - `duplicate_slug`, `duplicate_title`, `duplicate_alias`: 식별자/제목/별칭 변경
   - `primary_source_required`: 1차 출처 추가
   - `unknown_related_topic`: `related` 수정 또는 대상 모듈 추가
   - `published_duplicate`: 새 각도로 재작성하거나 `distinct_from` 검토
3. 경고만 있는 경우 `--strict`가 실패로 처리합니다. 경고도 수정하는 것을 권장합니다.
4. 수정 후 `validate --strict`와 `build --dry-run`을 로컬에서 통과시킵니다.

### 4.2 빌드는 성공했는데 큐가 비어 있음

증상: `Nothing was queued.`

가능한 원인:

- 오늘 쿼터가 이미 찼다 (`daily quota already filled`)
- 활성 주제가 모두 발행/대기 중이다
- 품질 게이트를 통과한 주제가 없다
- 모든 후보가 중복으로 제외되었다
- 슬롯이 모두 점유되었다

대응:

```bash
python -m astrion plan --date <YYYY-MM-DD> --json
python -m astrion status
python -m astrion list --status active
```

`skipped` 목록에서 사유를 확인합니다. "오늘 2개를 못 채웠다"는 그 자체로 장애가 아닙니다. 억지로 품질 미달 콘텐츠를 넣지 마십시오.

### 4.3 인증 실패 (`invalid_grant`, 401, 403)

증상: `publish.yml`이 healthcheck 또는 발행에서 실패. Issue에 `invalid_grant` 또는 `Blogger rejected the credentials`가 보임.

원인:

- Refresh token 만료 (External + Testing 앱은 7일)
- 비밀번호 변경, 계정 보안 이벤트
- OAuth 동의 철회
- 잘못된 Client ID/Secret
- Blogger 접근 권한이 없는 계정

대응:

1. `python -m astrion healthcheck`로 로컬에서 재현합니다.
2. `invalid_grant`면 `python -m astrion auth`를 다시 실행해 새 refresh token을 발급합니다.
3. GitHub Secret `BLOGGER_REFRESH_TOKEN`을 갱신합니다.
4. 7일마다 반복된다면 OAuth 앱을 **Publishing**으로 전환하거나 **Internal** 앱으로 옮깁니다.
5. `healthcheck`가 통과하면 `publish.yml`을 수동 실행합니다.

> 토큰을 재발급해도 큐와 상태는 그대로입니다. 다음 실행에서 due 항목이 정상 발행됩니다.

### 4.4 API 계약 변경 (`contract: changed` / `missing`)

증상: `healthcheck --strict-contract` 실패, 또는 주간 계약 워크플로가 PR/Issue 생성.

대응:

1. `python -m astrion contract --check`로 차이를 확인합니다.
2. 로컬에서 `python -m astrion contract --refresh`를 실행합니다.
3. `config/blogger_contract.json`의 diff를 검토합니다.
   - 메서드가 추가된 것: 대체로 안전
   - `posts.insert`의 `isDraft` 등 필수 파라미터가 사라진 것: **즉시 발행 중단**
   - 메서드 이름 변경/삭제: 코드 수정 필요
4. 주간 PR이 열려 있으면 내용을 확인하고 병합합니다.
5. 필수 표면이 사라졌다면 `astrion/blogger.py`와 테스트를 수정합니다. 이때는 자동 복구가 아니라 **의도적 코드 변경**입니다.
6. 수정 후 `healthcheck --strict-contract`가 통과하는지 확인합니다.

### 4.5 재시도 예약 (`retry_scheduled`)

증상: 발행 결과에 `retry_scheduled`, 상태에 `next_attempt_at`.

대응:

- 대부분 429/5xx/네트워크 오류입니다. 다음 실행을 기다립니다.
- `next_attempt_at`이 지나면 자동으로 다시 시도합니다.
- `attempts`가 `max_attempts`에 도달하면 `failed`로 전환되고 아카이브됩니다.
- 같은 항목이 반복해서 재시도되면 Blogger API 상태, 네트워크, 요청 크기를 확인합니다.

### 4.6 실패 페이로드

증상: `state.failed` 증가, `data/archive/failed/` 생성.

대응:

1. `.error.txt`를 읽어 원인을 확인합니다.
2. `.json`에서 `quality`, `review`, `blogger.labels`, `post.html_sha256`을 확인합니다.
3. `.html`을 브라우저에서 열어 렌더링을 확인합니다.
4. 원인별 조치:
   - 검증 실패: 모듈 수정 후 다시 빌드
   - 품질 미달: 콘텐츠 보강
   - 중복: 새 각도로 재작성 또는 `distinct_from` 검토
   - 일시적 API 오류: 재빌드 후 재발행
5. 실패 아카이브는 삭제하지 말고 원인 분석 자료로 남깁니다.

### 4.7 `in_flight`에 멈춘 항목

증상: `status`에 `in_flight`가 남아 있고 다음 실행이 `still in flight`로 건너뜀.

대응:

1. 다음 `publish.yml` 실행에서 `_reconcile_inflight`가 자동으로 처리합니다.
2. Blogger에서 해당 주제 마커(`data-astrion-topic="..."`)를 검색합니다.
3. 게시물이 있으면 상태에 기록되고 아카이브됩니다.
4. 없으면 `queued`로 되돌아가 다음 실행에서 발행됩니다.
5. 자동 조정이 반복 실패하면 Blogger API와 `state.json`을 수동으로 확인합니다. 수정 전 백업 필수.

### 4.8 중복 차단

증상: `duplicate topic`, `identical editorial signature`, `same primary keyword`, `shared alias`.

대응:

1. `python -m astrion check-duplicates`로 쌍을 확인합니다.
2. 정말 같은 글이면 새 글을 쓰지 않습니다.
3. 다른 각도라면 `core_question`, `explanation_target`, `central_principle`을 실제로 다르게 재작성합니다.
4. 그래도 겹치면 `distinct_from`에 상대 ID와 40자 이상의 사유를 적습니다.
5. `distinct_from`은 명시한 상대에게만 적용됩니다. 전체 면제가 아닙니다.

### 4.9 잘못된 글이 라이브로 나감

대응:

1. Blogger에서 해당 게시물을 **삭제**하거나 **초안으로 되돌립니다**.
2. `data/state.json`에서 해당 `published` 기록을 제거합니다.
3. 큐/아카이브는 기록으로 남깁니다.
4. 원인을 수정하고 재발행합니다.
5. 같은 실수가 반복되면 품질 규칙이나 워크플로를 강화합니다. 상태를 손으로 고치는 것만으로 끝내지 마십시오.

> 게시물을 삭제한 뒤 상태 기록을 남겨두면 이후 같은 주제가 영원히 중복으로 차단됩니다. 반드시 상태도 함께 정리합니다.

### 4.10 상태 파일 손상

증상: `state file is unreadable`, JSON 파싱 오류.

대응:

1. `git log -- data/state.json`으로 마지막 정상 커밋을 찾습니다.
2. `git show <commit>:data/state.json > data/state.json`으로 복원합니다.
3. 큐 파일과 `published` 기록이 일치하는지 확인합니다.
4. `python -m astrion status`로 파싱되는지 확인합니다.
5. 손상 원인(수동 편집, 동시 실행, 디스크 오류)을 제거합니다.

### 4.11 긴급 중지

즉시 발행을 멈추려면:

1. GitHub **Actions → Publish due posts → Disable workflow**.
2. 또는 `config/astrion.yaml`에서 `blogger.is_draft: true`로 되돌려 커밋합니다.
3. 이미 큐에 있는 항목은 다음 실행에서 초안으로만 생성됩니다.
4. 필요하면 `data/queue/*.json`을 다른 곳으로 옮겨 due 대기에서 제외합니다.
5. 원인 해결 후 워크플로를 다시 활성화합니다.

## 5. 콘텐츠 추가 절차

```bash
# 1. 파일 생성
# content/<category>/<slug>.yaml

# 2. 검증
python -m astrion validate
python -m astrion validate --strict

# 3. 중복 확인
python -m astrion check-duplicates

# 4. 미리보기
python -m astrion render <topic_id> --out dist/preview.html

# 5. 오늘 계획 확인
python -m astrion plan --date 2026-11-11

# 6. 큐 생성 없이 확인
python -m astrion build --date 2026-11-11 --dry-run

# 7. 실제 큐 생성
python -m astrion build --date 2026-11-11

# 8. 커밋
git add content data/state.json data/queue data/archive
git commit -m "content: add <topic>"
```

운영 중에는 `build.yml`이 매일 자동으로 큐를 만들므로, 콘텐츠 PR만 병합해도 다음 빌드에 반영됩니다.

## 6. 설정 변경

`config/astrion.yaml`에서 자주 바꾸는 값:

| 설정 | 의미 | 주의 |
| --- | --- | --- |
| `site.posts_per_day` | 하루 발행 수 (1~6) | `publish_slots` 수 이상이어야 함 |
| `site.publish_slots` | 발행 시각 | `HH:MM` 형식, 최소 `posts_per_day`개 |
| `site.timezone` | 기준 시간대 | 기본 `America/New_York` |
| `quality.publish_threshold` | PUBLISH 기준 | 낮추면 품질 기준 하락 |
| `quality.revise_threshold` | REVISE 기준 | PUBLISH보다 낮아야 함 |
| `quality.max_keyword_density` | 키워드 밀도 상한 | 0.001~0.10 |
| `blogger.is_draft` | 초안 모드 | 검증 후 `false` |
| `blogger.max_per_run` | 실행당 최대 발행 수 | 1~5, 기본 1 |
| `blogger.max_attempts` | 최대 시도 횟수 | 1~10 |
| `blogger.timeout_seconds` | API 타임아웃 | 5~120 |

변경 후:

```bash
python -m astrion validate --strict
python -m astrion build --dry-run
```

## 7. 수동 발행

특정 큐 항목만 발행:

```bash
python -m astrion publish --queue-id 2026-11-11-s1-example-topic --max 1
```

드라이런:

```bash
python -m astrion publish --dry-run --due
```

GitHub에서:

- **Actions → Publish due posts → Run workflow**
- `queue_id`를 비우면 due 전체, `dry_run`을 켜면 네트워크 호출 없음

## 8. 자격 증명 교체

1. 새 OAuth 클라이언트를 만들거나 기존 Secret을 재발급합니다.
2. `python -m astrion auth`로 새 refresh token을 받습니다.
3. GitHub Secrets 4개를 갱신합니다.
4. `python -m astrion healthcheck`로 로컬 검증합니다.
5. `publish.yml`을 `dry_run: true`로 실행합니다.
6. 정상 확인 후 실제 발행을 1회 테스트합니다.
7. 기존 토큰은 Google 계정 보안 설정에서 폐기합니다.

## 9. 백업과 복구

커밋 대상:

- `content/`
- `config/`
- `data/state.json`
- `data/queue/`
- `data/archive/`
- `.github/workflows/`

커밋되지 않는 것:

- `dist/` (미리보기, 리포트, 로그)
- `.venv/`
- 자격 증명

복구:

```bash
git log --oneline -- data/state.json
git show <commit>:data/state.json > data/state.json
python -m astrion status
```

큐 파일은 `queue_id`로 이름이 고정되어 있어 git에서 개별 복구가 쉽습니다.

## 10. 모니터링 체크리스트

### 매일

- [ ] `Build queue` 성공
- [ ] `Publish due posts` 성공 또는 실패 Issue 확인
- [ ] `status`에서 `Pending` 2개, `Failed` 0
- [ ] 새 게시물의 제목/라벨/수식/표 확인

### 매주

- [ ] 계약 갱신 PR 확인 (있으면 diff 검토)
- [ ] 실패 아카이브와 Issue 정리
- [ ] 큐에 오래 남은 항목 확인
- [ ] 발행 주제가 카테고리별로 균형 있는지 확인

### 매월

- [ ] OAuth 앱 상태(Testing/Publishing, 7일 만료) 확인
- [ ] 발행 수와 품질 점수 추세
- [ ] 중복 경고/예외(`distinct_from`) 누적 검토
- [ ] GitHub Actions 사용량과 실패율
- [ ] `content/`의 오래된 `draft` 모듈 정리

## 11. 비용과 한도

- GitHub Actions: 공개 저장소는 무료, 비공개 저장소는 실행 시간이 과금됩니다. 하루 2회 빌드 + 매시간 발행은 가볍습니다.
- Blogger API: Google API 할당량이 적용됩니다. Astrion은 시간당 최대 1회, 실행당 1개 발행이므로 일반적으로 여유가 있습니다.
- 외부 AI API 비용: **0**. 사용하지 않습니다.

## 12. 문제가 생겼을 때의 원칙

1. **멈춥니다.** `is_draft: true` 또는 워크플로 비활성화.
2. **보존합니다.** 큐, 상태, 아카이브, 로그를 삭제하지 않습니다.
3. **관찰합니다.** Issue, `status`, `healthcheck`, 아카이브를 확인합니다.
4. **최소 수정합니다.** 원인을 고치고, 상태를 손으로 고치는 것은 최후의 수단으로 둡니다.
5. **다시 검증합니다.** `validate --strict`, `build --dry-run`, `publish --dry-run`, healthcheck.
6. **기록합니다.** 같은 문제가 반복되면 문서와 테스트를 갱신합니다.

# model-tf

[English](./README.md) · **한국어**

AI 코딩 모델의 사실 층(`model-facts.json`)과 평가 층(`model-tiers.json`)을 관리하는 공개 데이터 저장소입니다. [UsageSwitch](https://github.com/deximple/usageswitch)의 Suggestion 기능(및 POSA 등 외부 도구)이 참조하는 공통 데이터 계층 역할을 합니다.

| 층 | 내용 | 파일 | 출처 | 갱신 |
|---|---|---|---|---|
| 사실 | 모델 스펙·기능·가격·월 한도 (출처 URL·확인일 포함) | `model-facts.json` (49개 모델, 15개 제조사) | 6개 벤더 공식 문서 | GitHub Actions 매일 자동 반영 |
| 평가 | 모델×강도별 티어(`frontier`/`mid`/`low`)·축별 상대 순위·근거 | `model-tiers.json` (71개 엔트리) | 4대 독립 벤치마크 리더보드 | GitHub Actions 조건부 자동 반영 |
| 상태 | 계정별 창 잔량·차감 폭·리셋 이력 | 각 앱 로컬 저장소 | 수집기·로컬 세션 로그 | 앱 내부 |

## 디렉터리 구조

```text
model-tf/
  model-facts.json                  # 사실 층 (POSA version: 1 모양 호환)
  model-tiers.json                  # 평가 층 (version: 1)
  schema/
    model-facts.schema.json         # 사실 층 JSON Schema (draft 2020-12)
    model-tiers.schema.json         # 평가 층 JSON Schema (draft 2020-12)
  sources/
    fetch_vendor_facts.py           # 6대 공식 문서 기반 사실 층 자동 수집기
    fetch_artificial_analysis.py    # 4대 벤치마크 기반 평가 층 자동 수집·합성기
    check_staleness.py              # 변경 요약(--diff-head), 게이트 판정(--gate-out), 노후 검사
    hold_tier_shifts.py             # 단일 출처 티어 변경만 PR로 보류하고 나머지는 main 반영
    refresh_common.py               # 재시도 fetch, --status-out 소스 상태 보고 공통 모듈
  scripts/
    validate.py                     # 스키마 + POSA 규칙 + 교차 정합성 검증기
  tests/
    test_repo.py                    # 단위 테스트 (38개)
  .github/workflows/
    validate.yml                    # push / PR 검증 워크플로
    refresh.yml                     # 매일 예약 + 수동 실행 무인 갱신 워크플로 (selftest가 재사용 호출)
    selftest.yml                    # 임시 브랜치에서 갱신 경로 전체를 실제로 검증하는 자가 검증 워크플로
    auto-repair.yml                 # 파서 고장 시 코딩 에이전트(기본: Copilot CLI)가 수정 PR을 제안 (검증·게시 분리)
    selftest-repair.yml             # 실제 고장을 재현해 자동 수리를 끝까지 검증
```

## 핵심 규칙

1. **`vendorId`는 하네스나 구독이 아니라 모델 제조사(maker)입니다.**
   - Claude Code, Cursor, Devin 어디서 호출하든 Claude 모델은 `claude/claude-opus-5-5`입니다.
   - 제조사 ID는 `claude`, `openai`, `google`, `xai`, `zhipuai`, `moonshotai`, `deepseek`, `alibaba`, `minimax`, `xiaomi`, `stepfun`, `tencent`, `meta`, `meituan` 및 미상 커뮤니티 무료 모델용 `opencode`를 사용합니다.
2. **사실 층(`model-facts.json`)에는 스펙과 가격·한도만 담습니다.**
   - 모든 값은 `https://` 출처(`source`)와 확인일(`checkedAt`)을 가집니다.
   - 순위·추천·강점 어휘(`recommend`, `rank`, `best`, `strength`)나 한글·문장형 텍스트는 금지됩니다.
3. **평가 층(`model-tiers.json`)의 단위는 모델×강도(`key` × `effort`)입니다.**
   - 사실 층의 `efforts`가 빈 배열(`[]`)이면 `effort: "default"`를 쓰고, 비어 있지 않으면 해당 `efforts` 안의 값만 씁니다.
   - 작업 축(`axis`)은 `simpleCoding`과 `reasoning` 두 축이며, `1`이 최상위 순위입니다.
   - `community` 근거 단독으로는 티어를 부여할 수 없으며 최소 1개 이상의 `benchmark` 또는 `vendor` 근거가 필수입니다.

## 자동 수집 출처

### 1. 사실 층 (`sources/fetch_vendor_facts.py`)
1차 모델 제조사 공식 문서를 우선 적용하고, OpenCode Go의 플랜별 월 한도(`monthlyLimitUSD/{go,go-plus,go-teams}`)와 카탈로그 모델을 병합합니다.
- **Anthropic Claude:** `platform.claude.com/docs/en/models/overview.md`, `build-with-claude/effort.md`
- **OpenAI / Codex:** `developers.openai.com/api/docs/models` (+ 모델별 상세), `learn.chatgpt.com/docs/models`
- **Google Gemini:** `ai.google.dev/gemini-api/docs/pricing.md.txt`, `models/<slug>.md.txt`
- **xAI Grok:** `docs.x.ai/docs/models.md`
- **Z.ai / Zhipu GLM:** `docs.z.ai/guides/overview/pricing.md`, `guides/llm/glm-5.3.md`
- **OpenCode Go:** `opencode.ai/docs/go/`

### 2. 평가 층 (`sources/fetch_artificial_analysis.py`)
4개 공신력 있는 독립 벤치마크를 결합해 교차 근거(`evidence`)와 축별 순위(`simpleCoding`, `reasoning`)를 산출합니다.
1. **Artificial Analysis** (`artificialanalysis.ai/leaderboards/models`): Intelligence Index(`reasoning`) + Terminal-Bench 4.0 & FrontierCode 2.0 합성(`simpleCoding`)
2. **Stanford / Laude Terminal-Bench** (`www.tbench.ai/leaderboard`): 에이전트 터미널 코딩 정확도(330회 시행)
3. **Vals AI** (`www.vals.ai/benchmarks/vals_index`): 기업 코딩·법률·금융·멀티모달 종합 `Vals Index`
4. **Scale AI SEAL** (`scale.com/leaderboard`): `SWE Atlas`, `MCP Atlas`, `HLE Diamond`, `MultiChallenge`, `Humanity's Sixth Sense`

## 무인 자동 업데이트 (`refresh.yml`)

- **매일 21:37 UTC(06:37 KST)** 와 `workflow_dispatch` 수동 실행으로 동작합니다. 정각(`:00`)은 GitHub 부하로 예약 실행이 지연·누락되므로 일부러 피했습니다.
- `fetch_vendor_facts.py` → `fetch_artificial_analysis.py` → `scripts/validate.py` → `unittest`(38개) 순서로 검증합니다.
- **조건부 반영 (`mode=conditional`, 기본값):**
  - 사실 층 변경, 신규 모델 편입, 순위(`rank`) 갱신, **2개 이상 독립 벤치마크 근거가 확인된 티어(`tier`) 변경**은 `main`에 즉시 커밋·푸시됩니다.
  - 기존 모델의 `tier`가 **단일 출처 근거만으로** 바뀐 경우에는 **그 티어 값만 보류**합니다. 나머지 변경은 모두 `main`에 반영되고, `refresh/model-data` 리뷰 PR에는 보류된 티어 변경만 담깁니다. 리뷰 PR이 열려 있어도 데이터 갱신은 멈추지 않으며, 해당 변경이 사라지거나 확정되면 PR은 자동으로 닫힙니다.
  - `mode=direct`는 전부 `main`에, `mode=pr`은 전부 리뷰 PR에 반영합니다.
- **소스 장애가 갱신을 멈추지 않습니다:** 모든 요청은 일시 오류(5xx/429/타임아웃)를 백오프로 재시도합니다. 그래도 특정 벤더 문서나 리더보드가 실패하거나 0행으로 파싱되면, 그 소스의 기존 값·근거는 (기존 `checkedAt` 그대로) 유지하고 정상 소스만 갱신합니다.
- **조용히 실패하지 않습니다:** 단계 실패, 소스 장애, PR 생성 실패가 생기면 `refresh-alert` 라벨 이슈를 하나 열거나 갱신하고, 이후 모든 소스가 정상인 실행에서 자동으로 닫습니다.
- **봇 커밋도 CI 검증:** `GITHUB_TOKEN`으로 만든 push/PR은 다른 워크플로를 트리거하지 않으므로, refresh가 직접 `validate.yml`을 `workflow_dispatch`로 실행합니다.
- **예약 유지:** 공개 저장소는 60일간 활동이 없으면 예약 워크플로가 비활성화됩니다. 세 가지 독립 장치로 막습니다.
  - **45일 `--touch`:** 실시간 재검증 후 `checkedAt`을 갱신합니다.
  - **하트비트 커밋:** `main`에 50일간 커밋이 없으면 `.github/refresh-heartbeat` 파일을 커밋합니다. 모든 소스가 실패 중이어도 동작합니다.
  - **API 재활성화:** 매 실행마다 워크플로를 다시 활성화합니다.
- **1회 설정 필요(리뷰 PR 경로):** *Settings → Actions → General → Workflow permissions → "Allow GitHub Actions to create and approve pull requests"* 를 켜 두세요. 꺼져 있으면 PR 단계만 실패하고(알림 이슈에 안내), `main` 반영은 정상 진행됩니다.

## 자동 수리 (`auto-repair.yml`)

갱신 중 어떤 소스가 코드로 고칠 수 있는 방식으로 실패하면, 코딩 에이전트가 수정안을 리뷰 PR로 올립니다. 파서가 아무것도 읽지 못하거나 페이지가 옮겨간 경우가 여기에 해당하고, 사이트 장애·속도 제한·TLS 오류는 대상이 아닙니다.

- **시크릿이 필요 없습니다.** 기본 에이전트는 [GitHub Copilot CLI](https://github.com/github/copilot-cli)입니다. 잡 자체의 `GITHUB_TOKEN`에 `copilot-requests: write` 권한을 주어 실행하므로, 저장소의 GitHub Copilot 요금제 안에서 처리됩니다. 모델은 요금제에서 쓸 수 있는 것 중 Copilot이 고르며, 고정하려면 저장소 변수 `AUTO_REPAIR_COPILOT_MODEL`을 지정하세요.
- **에이전트는 쓰기 권한을 갖지 않습니다.** `propose` 잡은 읽기 전용 토큰만 쓰고 git 자격 증명이 없습니다. 에이전트는 파일 편집과 `python3` 실행만 할 수 있고 `git push`·`gh`는 막혀 있으며, 에이전트가 실행하는 명령의 환경에는 토큰이 없습니다.
- **채택 여부는 에이전트가 아니라 결정론적 검사가 정합니다.** 다음을 모두 만족할 때만 수정안을 채택합니다.
  - 변경이 `sources/`·`tests/` 안에만 있음
  - 단위 테스트와 `validate.py` 통과
  - 다시 수집했을 때 대상 소스가 모두 `ok`
  - 패치의 비밀값 검사 통과
- **게시 단계는 수정된 코드를 실행하지 않습니다.** `publish` 잡이 검증된 패치만 적용해 `auto-repair` 라벨 PR을 엽니다. 자동 병합은 하지 않습니다.
- **같은 장애에는 한 번만 시도합니다.** 결과는 `refresh-alert` 이슈에 댓글로 남고, 열린 PR이 있거나 같은 장애를 이미 시도했으면 건너뜁니다. 에이전트 실행 자체가 실패하면(예: Copilot 사용량 소진) 다음 갱신 때 다시 시도합니다.
- **Claude Code로 바꾸려면(선택):** 저장소 변수 `AUTO_REPAIR_ENGINE`을 `claude`로 지정하고, *Settings → Secrets and variables → Actions*에 시크릿 `ANTHROPIC_API_KEY` 또는 `CLAUDE_CODE_OAUTH_TOKEN`을 추가하세요. 이 경우 비용은 GitHub이 아니라 Anthropic에서 청구됩니다.
- **수동 실행:** *Actions → auto-repair → Run workflow*
- **라이브 검증:** *Actions → selftest-repair → Run workflow*. 임시 브랜치에 2026-11-16의 Vals 고장을 재현한 뒤, 에이전트의 수정이 검증된 PR로 도착하는지 확인합니다. 실행할 때마다 에이전트 세션 1회를 씁니다.

## 자가 검증 (`selftest.yml`)

`refresh.yml`이나 저장소 Actions 설정을 바꾼 뒤 **Actions → selftest → Run workflow**로 실행하세요. 실제 `refresh.yml`을 임시 브랜치 `selftest/base`에서 세 번 돌리고(`main`은 건드리지 않음), 단계마다 결과를 검사합니다.

1. `docs.x.ai`·`scale.com` 장애를 흉내 내고 단일 출처 티어 변경을 주입합니다. 다음을 확인합니다.
   - 보류 커밋이 임시 브랜치에 반영됨
   - 리뷰 PR에는 티어 변경만 담김
   - 실패 소스의 데이터가 이전 값으로 유지됨
   - 봇이 만든 두 커밋에서 `validate` 통과
   - 알림 이슈가 생성됨
2. 같은 상황을 다시 돌립니다. 다음을 확인합니다.
   - 새 커밋이 생기지 않음
   - PR 내용이 그대로임
   - 알림이 중복 게시되지 않음
3. 실제 소스로 `mode=direct`, 유휴 기준 0일로 돌립니다. 그날의 실데이터와 무관하게 결과가 같습니다. 다음을 확인합니다.
   - 대체된 PR이 자동으로 닫힘
   - 복구된 알림이 자동으로 닫힘
   - `--touch` 재검증으로 모든 `checkedAt`이 오늘로 갱신됨
   - 하트비트 커밋이 생성됨

   이때 실제 소스가 장애 중이면 알림은 정상적으로 열린 채 남고, 자가 검증은 실패로 표시됩니다.

끝나면 만든 브랜치·PR·이슈·라벨을 모두 지웁니다. 약 5분 걸립니다.

## 로컬 실행 명령어

```sh
python3 scripts/validate.py                            # 스키마 및 교차 정합성 검사
python3 -m unittest discover -s tests -v               # 단위 테스트 실행
python3 sources/fetch_vendor_facts.py                  # 공식 문서에서 model-facts.json 갱신
python3 sources/fetch_artificial_analysis.py           # 4대 벤치마크에서 model-tiers.json 갱신
python3 sources/check_staleness.py --diff-head         # 변경 요약 및 노후 항목 리포트
```

# model-tf

[English](./README.md) · **한국어**

AI 코딩 모델의 사실 층(`model-facts.json`)과 평가 층(`model-tiers.json`)을 관리하는 공개 데이터 저장소입니다. [UsageSwitch](https://github.com/deximple/usageswitch)의 Suggestion 기능(및 POSA 등 외부 도구)이 참조하는 공통 데이터 계층 역할을 합니다.

| 층 | 내용 | 파일 | 출처 | 갱신 |
|---|---|---|---|---|
| 사실 | 모델 스펙·기능·가격·월 한도 (출처 URL·확인일 포함) | `model-facts.json` (49개 모델, 15개 제조사) | 6개 벤더 공식 문서 | GitHub Actions 주간 자동 반영 |
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
  scripts/
    validate.py                     # 스키마 + POSA 규칙 + 교차 정합성 검증기
  tests/
    test_repo.py                    # 단위 테스트 (12개)
  .github/workflows/
    validate.yml                    # push / PR 검증 워크플로
    refresh.yml                     # 주간 예약 + 수동 실행 무인 갱신 워크플로
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
3. **Vals AI** (`www.vals.ai/benchmarks`): 기업 코딩·법률·금융·멀티모달 종합 `Vals Index`
4. **Scale AI SEAL** (`scale.com/leaderboard`): `SWE Atlas`, `MCP Atlas`, `HLE Diamond`, `MultiChallenge`, `Humanity's Sixth Sense`

## 무인 자동 업데이트 (`refresh.yml`)

- 매주 월요일 06:00 UTC(및 `workflow_dispatch` 수동 실행)에 동작합니다.
- `fetch_vendor_facts.py` → `fetch_artificial_analysis.py` → `scripts/validate.py` → `unittest`(12개) 순서로 검증합니다.
- **조건부 자동 반영 (`mode=conditional`, 기본값):**
  - 사실 층 변경, 신규 모델 편입, 순위(`rank`) 갱신, **2개 이상 독립 벤치마크 근거가 확인된 티어(`tier`) 변경**은 `main` 브랜치에 즉시 커밋·푸시됩니다.
  - 기존 모델의 `tier`가 **단일 출처(1개) 근거만으로** 바뀐 경우에만 `refresh/model-data` 브랜치로 리뷰 PR을 엽니다.
- **45일 하트비트 (`--touch`):** 마지막 커밋 이후 45일이 지나면 모든 출처를 실시간 재검증한 뒤 `checkedAt`과 번들 `date`를 갱신·커밋하여 GitHub Actions의 60일 비활성 스케줄 중지를 방지합니다.

## 로컬 실행 명령어

```sh
python3 scripts/validate.py                            # 스키마 및 교차 정합성 검사
python3 -m unittest discover -s tests -v               # 단위 테스트 실행
python3 sources/fetch_vendor_facts.py                  # 공식 문서에서 model-facts.json 갱신
python3 sources/fetch_artificial_analysis.py           # 4대 벤치마크에서 model-tiers.json 갱신
python3 sources/check_staleness.py --diff-head         # 변경 요약 및 노후 항목 리포트
```

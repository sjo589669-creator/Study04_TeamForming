# 팀 프로젝트 팀 편성 앱

작성자: 조서형

## 1. 배포 주소
https://study04-teamforming.streamlit.app

## 2. PRD와 구현 계획의 위치
- `docs/PRD.md`
- `docs/구현 계획.md`

## 3. 고른 해결 방법
| 기능 | 고른 해결 | 내용 |
|---|---|---|
| 다른 사람의 변경 보기 | 어려운 해결 | `st.fragment(run_every=3)` 로 학생 화면과 관리자 화면의 팀 현황을 3초마다 그 부분만 다시 그림 |
| 필수 포함 역할을 지킬 수 없는 팀 | 어려운 해결 | 지원 단계에서 "들어간 뒤 남는 자리 < 아직 못 채운 필수 역할 수"이면 막음(`rules.join_block_reason`). 배정 단계에서도 배정이 없으면 이유를 안내 |
| 같은 팀에 대기 중인 지원이 여럿 | 어려운 해결 | 모든 팀원이 찬성한 순간 정원·주 역할 중복·필수 역할 자리를 다시 검사(투표가 끝난 순서대로 처리) |
| 마지막 자리에 두 명이 동시에 들어감 | 어려운 해결 | `tf_members` 의 `(team_id, slot_no)` 기본 키와 `student_id` 유일 제약(`schema.sql`). 팀 행 잠금으로 처리 순서도 맞춤 |
| AI 응답이 늦을 때 | 어려운 해결 | 요청을 작업 스레드에서 하고, 부른 때부터 전체 120초가 지나면 끊고 다음 모델로 넘어감(`ai.call_model`) |
| 3차 모델까지 실패 | 어려운 해결 | 백트래킹으로 모든 경우를 따져 검사 다섯 가지를 모두 통과하는 배정을 찾음(`rules.solve_assignment`) |
| AI 대기 중 화면 | 어려운 해결 | `assign_status` 에 "2차 모델(모델 이름)의 응답을 기다리는 중… 37초 / 120초" 처럼 몇 차 모델인지와 경과 시간을 보여 줌 |
| DB 일시정지 막기 | 어려운 해결 | GitHub Actions 예약 작업(`.github/workflows/keepalive.yml`)이 매일 09:17(한국 시각)에 `tf_settings` 에 질의 |

## 4. 쓴 모델 3개
- 1차 `google/gemma-4-26b-a4b-it:free`
- 2차 `cohere/north-mini-code:free`
- 3차 `nvidia/nemotron-3.5-lightning:free`

무료 모델 목록이 며칠 사이에도 바뀌어서 5항의 시험을 거쳐 정했다. 한 회사의 무료 제공이 막혀도 버티도록 세 모델을 서로 다른 회사(Google, Cohere, NVIDIA)에서 골랐다. 무료 모델 자동 연결 라우터 `openrouter/free` 는 공고 6.3절 14번의 감점 대상이라 쓰지 않는다(6항 5번).

## 5. 오픈라우터 AI 모델 호출 실패 사례
**세 모델의 무료 버전이 모두 내려가 HTTP 404 (2026-10-31 22:48, 실행 `1031-134822-46e1`)**

배포한 앱에서 어려움 명단을 올리고 바로 마감한 뒤 AI 배정을 눌렀다. 처음 고른 세 모델이 모두 0.1초 만에 같은 오류로 실패했다.

| 차수 | 모델 | 결과 |
|---|---|---|
| 1차 | `nvidia/nemotron-3-super-120b-a12b:free` | HTTP 404: This model is unavailable for free. The paid version is available now |
| 2차 | `openai/gpt-oss-120b:free` | 위와 같음 |
| 3차 | `z-ai/glm-4.5-air:free` | 위와 같음 |

- 앱의 처리: 실패할 때마다 다음 모델로 넘어갔고, 3차까지 실패하자 앱이 모든 경우를 따져 규칙을 지키는 배정을 만들었다. 실패 처리는 설계대로 동작했다.
- 화면: `img/ai-404-progress.png`(배정 진행 안내), `img/ai-404-log.png`(AI 호출 기록)
- 원인: 같은 이름의 `:free` 모델이 무료 제공을 끝내고 유료 버전만 남김
- 대응: 오픈라우터의 무료 모델 목록을 다시 확인해 `nvidia/nemotron-3-ultra-550b-a55b:free`, `google/gemma-4-31b-it:free`, `poolside/laguna-s-2.1:free` 로 바꿨다(커밋 `772740c`).

**바꾼 세 모델이 시간 초과·429·404로 각각 다르게 실패 (2026-10-31 22:57)**

| 차수 | 모델 | 결과 |
|---|---|---|
| 1차 | `nvidia/nemotron-3-ultra-550b-a55b:free` | 120초 안에 응답하지 않아 앱이 끊음 |
| 2차 | `google/gemma-4-31b-it:free` | HTTP 429: Provider returned error (모델을 제공하는 회사가 요청을 거절) |
| 3차 | `poolside/laguna-s-2.1:free` | HTTP 404: no provider serves this model anymore |

- 앱의 처리: 1차에서 전체 120초가 지나자 기다리지 않고 끊고 2차로 넘어갔고(4.3절 시간 제한), 3차까지 실패하자 앱이 직접 배정했다.
- 화면: `img/ai-run2-timeout-429-404.png`
- 원인: 무료 모델은 회사 한 곳에서만 제공되어 그 회사가 느리거나 막히면 대신할 곳이 없고, 무료 목록이 며칠 만에도 바뀜(2주 전 목록에 있던 3차 모델이 사라짐)
- 대응: 1차는 빠른 모델 `nvidia/nemotron-3.5-lightning:free`, 2차는 `thinkingmachines/inkling:free`, 3차는 특정 모델 대신 `openrouter/free` 로 바꿨다(커밋 `7f82a7a`).

**1차 빈 응답, 2차 403, 3차 성공 (2026-10-31 23:09)**

| 차수 | 모델 | 결과 |
|---|---|---|
| 1차 | `nvidia/nemotron-3.5-lightning:free` | 76초 뒤 빈 응답(내용 없이 끝남) |
| 2차 | `thinkingmachines/inkling:free` | HTTP 403: is only available on agentic harnesses (코딩 에이전트 앱에서만 쓸 수 있음) |
| 3차 | `openrouter/free` | 74초 만에 배정안을 냈고 검사 다섯 가지를 통과 |

- 앱의 처리: 빈 응답도 실패로 보고 다음 모델로 넘어갔고(4.3절), 3차 모델의 배정안을 검사한 뒤 관리자에게 보여 줬다.
- 화면: `img/ai-run3-success.png`
- 원인: 1차는 추론 모델이 답을 쓰기 전에 끝난 것으로 보이고, 2차는 이 앱처럼 직접 부르는 방식으로는 쓸 수 없는 모델이었음
- 대응: 2차는 매번 403이 날 것이므로 `google/gemma-4-26b-a4b-it:free` 로 바꿨다(커밋 `dce4cb4`). 그 뒤 3차의 `openrouter/free` 가 감점 대상임을 알고(6항 5번), 세 모델을 4항의 모델로 다시 정했다(커밋 `b3cca07`).

**1차 빈 응답, 2차 429, 3차 빈 응답 → 앱 직접 배정 (2026-10-31 23:5x)**

부록 3(어려움) 1~8단계를 배포한 앱에서 해 본 뒤 마감하고 AI 배정을 눌렀다. 화면 위쪽의 모델 목록을 보면, 커밋 `b3cca07` 이 아직 배포되기 전이라 커밋 `dce4cb4` 의 모델로 실행되었다.

| 차수 | 모델 | 결과 |
|---|---|---|
| 1차 | `nvidia/nemotron-3.5-lightning:free` | 112초 뒤 빈 응답 |
| 2차 | `google/gemma-4-26b-a4b-it:free` | HTTP 429: Provider returned error |
| 3차 | `openrouter/free` | 86초 뒤 빈 응답 |

- 앱의 처리: 세 번 모두 실패로 보고 넘어갔고, 3차까지 실패하자 앱이 모든 경우를 따져 배정했다. 앞 단계에서 이미 팀에 들어간 학생은 그대로 두고, 팀이 없는 학생만 배정했다.
- 화면: `img/ai-run4-empty-429-empty.png`
- 원인: 앞의 23:09 시험에서 성공한 `openrouter/free` 도 이번에는 빈 응답을 냈다. 무료 모델은 같은 모델이라도 때에 따라 결과가 다르다. 그래서 3차까지 실패할 때의 앱 직접 배정이 꼭 필요하다.

## 6. 클로드가 잘못된 추론이나 답변을 한 사례
근거는 이 앱을 만든 클로드 대화 기록(2026-10-27~31)과, 각 항목에 적은 화면·커밋이다.

1. **깃허브에 올리지 못한 원인을 잘못 짚음.** 저장소를 만든 뒤 클로드가 코드를 올리지 못하자 "깃허브 계정을 Claude에 연결하면 된다"고 두 번 안내했다. 연결한 뒤에도 같은 오류가 나서 클로드가 다시 확인해 보니, 비공개 저장소에는 Claude 깃허브 앱이 따로 설치되어 있어야 했다(GitHub Settings → Installed GitHub Apps가 비어 있었음). 앱을 Study04_TeamForming 하나에만 설치한 뒤에야 첫 커밋 `9393b81` 을 올릴 수 있었다.
   - 알아챈 방법: 안내대로 연결했는데도 같은 오류가 반복됨
2. **배포 화면의 다른 칸에 입력함.** Streamlit 배포 화면에서 클로드가 저장소 이름을 Repository 칸이 아니라 Main file path 칸에 입력해, 그 칸이 `streamlit_app.pysjo589669-creator/Study04_TeamForming` 이 되었다.
   - 알아챈 방법: 입력한 뒤 찍은 화면에서 칸 내용이 틀린 것을 보고 다시 입력함
3. **오픈라우터 로그인 방법을 잘못 안내함.** 클로드는 깃허브 단추로 오픈라우터에 로그인하라고 했지만, 그 깃허브 계정으로 만든 오픈라우터 계정이 없어 "The External Account was not found" 가 떴다.
   - 알아챈 방법: 로그인 화면의 오류 문구
4. **지금도 무료인지 확인하지 않은 모델을 기본값으로 정함.** 클로드는 개발 환경에서 오픈라우터에 접속할 수 없어 모델 목록을 확인하지 못한 채, 알고 있던 모델 이름 세 개를 기본값으로 넣었다(그 사실은 그때 함께 알렸다). 실제로 돌려 보니 세 모델 모두 무료 제공이 끝나 있었다(5항).
   - 알아챈 방법: 배포한 앱의 AI 호출 기록(`img/ai-404-log.png`)
5. **공고가 감점하는 라우터를 3차 모델로 넣음.** 모델이 자꾸 사라지자 클로드는 "모델이 사라져도 이름이 남는다"며 3차 모델을 `openrouter/free` 로 바꿨다(커밋 `7f82a7a`). 그러나 공고 6.3절 14번은 무료 모델 자동 연결 라우터 `openrouter/free` 를 쓰면 2점을 감점한다. 클로드는 이 라우터로 AI 배정이 성공하자(`img/ai-run3-success.png`) 이를 좋은 해결로 안내했다.
   - 알아챈 방법: 보고서(이 README)를 7.1절 양식에 맞추려고 공고를 다시 읽다가 6.3절 감점표에서 발견함. 특정 `:free` 모델 세 개로 다시 바꿈(커밋 `b3cca07`)

## 7. 제출한 앱의 문제점이나 개선 필요 사항
1. **로컬 시험에서 실제 라이브러리를 쓰지 못함.** 개발 환경에서 Streamlit·psycopg·bcrypt를 설치할 수 없어, `tests/` 의 시나리오 시험은 직접 만든 대체 DB 드라이버(`tests/pgshim.py`)와 대체 해시, 가짜 Streamlit으로 돌렸다. 그래서 실제 라이브러리로는 배포한 앱에서 사람이 직접 확인했다(2026-10-31: 관리자의 명단 올리기·마감·AI 배정, 연습용 어려움 시나리오 1~8단계의 지원·투표·승인 직전 재검사, 결과가 모두 기대와 같았음). 9~15단계와 중간·쉬움 시나리오는 배포한 앱에서 끝까지 돌려 보지 않았다. 개선: 실제 라이브러리를 설치할 수 있는 환경에서 같은 자동 시험을 돌려야 함
2. **AI 배정 중에는 관리자 화면이 멈춤.** 부록 3(어려움) 명단을 올리고 마감한 뒤 15단계의 AI 배정을 눌렀을 때(2026-10-31 22:57), 1차 모델의 응답을 120초 동안 기다리는 사이 관리자 화면은 진행 안내만 바뀌고 다른 단추를 쓸 수 없었다(화면 `img/ai-run2-timeout-429-404.png`, 오른쪽 위에 실행 중 표시). 배정은 단추를 누른 그 화면 실행 안에서 모델을 차례로 부르므로(`streamlit_app.py` 의 `run_assign`), 모델 3개가 모두 120초를 넘기면 최대 6분 동안 멈출 수 있다. 개선: 배정을 별도 작업으로 돌리고 화면은 진행 상황만 읽게 함
3. **배정 결과가 늘 같음.** 앱이 직접 배정할 때(`rules.solve_assignment`)는 정해진 순서로 처음 찾은 배정을 쓰므로, 같은 상태에서는 늘 같은 배정이 나오고 팀 사이의 역할 균형은 따지지 않는다. 개선: 규칙을 지키는 배정 가운데 보조 역할이 고르게 퍼진 배정을 고르게 함
4. **새로고침하면 로그인이 풀림.** 로그인 정보를 브라우저 탭의 세션(`st.session_state`)에만 두므로, 새로고침하거나 앱이 다시 배포되면 처음 로그인 화면으로 돌아간다(2026-10-31 모델을 바꾼 뒤 새로고침했을 때 겪음). 지원·투표·배정 같은 데이터는 DB에 있어 사라지지 않는다. 개선: 로그인 토큰을 쿠키에 담아 새로고침 뒤에도 로그인을 유지함
5. **3초마다 DB를 읽음.** 학생 화면과 관리자 팀 현황이 3초마다 DB를 다시 읽으므로, 접속한 사람이 많으면 Supabase 무료 프로젝트의 연결 수와 요청 수에 부담이 된다. 개선: 바뀐 것이 있을 때만 다시 읽음(예: 마지막 변경 시각을 먼저 확인)

---

## 실행 방법

### 로컬
```bash
pip install -r requirements.txt
cp .env.example .env      # 값을 채움. 파일 이름은 정확히 .env
streamlit run streamlit_app.py
```
앱이 시작할 때 `schema.sql` 을 실행해 tf_ 테이블을 만들고, `ADMIN_PASSWORD` 로 관리자 계정(학번 `admin`)을 만들거나 비밀번호를 갱신합니다.

### Streamlit Secrets (배포)
```toml
OPENROUTER_API_KEY = "sk-or-v1-..."
DATABASE_URL = "postgresql://postgres.xxxx:비밀번호@aws-0-....pooler.supabase.com:6543/postgres"
ADMIN_PASSWORD = "..."
# AI_MODELS = "모델1,모델2,모델3"   # 선택
```
Streamlit Cloud 와 GitHub Actions 는 IPv4만 되므로 DATABASE_URL 은 Supabase의 **pooler** 주소를 씁니다.

### DB 일시정지 막기
저장소 Settings → Secrets and variables → Actions 에 `DATABASE_URL` 을 넣고, Actions 탭에서 `supabase-keepalive` 를 한 번 수동 실행해 성공하는지 확인합니다.

### 시나리오 시험(로컬 PostgreSQL)
`tests/run_scenarios.py` 는 연습용 시나리오 3개의 모든 단계를 화면 단추가 부르는 함수로 실행해 결과 안내의 첫 단어를 비교합니다(127.0.0.1:5433, trust 인증 PostgreSQL 필요).

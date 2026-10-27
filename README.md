# 팀 프로젝트 팀 편성 앱

작성자: 조서형

> ✏️ 표시가 있는 곳은 배포·시험한 뒤 직접 채워야 하는 칸입니다. 5~7항은 실제로 겪은 일과 그 화면·커밋만 적어야 합니다(꾸며 내면 시나리오 밖 점수 0점).

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
✏️ (Streamlit Secrets 의 `AI_MODELS` 로 바꿨다면 그 이름으로 고칠 것)
- 1차 `nvidia/nemotron-3-super-120b-a12b:free`
- 2차 `openai/gpt-oss-120b:free`
- 3차 `z-ai/glm-4.5-air:free`

## 5. 오픈라우터 AI 모델 호출 실패 사례
✏️ 관리자 화면의 "배정 진행 안내"와 "AI 호출 기록"(tf_ai_logs)을 캡처해 `img/` 에 넣고 적을 것.
예: "1차 모델이 HTTP 429로 거절해 2차 모델로 넘어감(화면 `img/ai-429.png`)"

## 6. 클로드가 잘못된 추론이나 답변을 한 사례
✏️ 실제로 겪은 사례와 알아챈 방법(화면이나 커밋 해시)을 적을 것.

## 7. 제출한 앱의 문제점이나 개선 필요 사항
✏️ 연습용 시나리오를 해 보며 찾은 문제와 그 단계·화면을 적을 것. 후보(직접 확인한 뒤에만 적을 것):
- AI 배정 중에는 관리자 화면 전체가 그 작업을 하느라 다른 단추를 누를 수 없음
- 모든 경우 탐색 배정은 정해진 순서로 찾으므로 같은 상태면 늘 같은 배정이 나옴(무작위성·균형 고려 없음)

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

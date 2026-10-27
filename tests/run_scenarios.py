"""연습용 시나리오 3개(공고 부록)를 로컬 PostgreSQL 에서 단계별로 실행해 기대 결과와 비교한다.

실행: python3 tests/run_scenarios.py   (127.0.0.1:5433 에 trust 인증 PostgreSQL 이 있어야 함)
화면은 거치지 않고 화면 단추가 부르는 함수(store.apply, store.vote …)를 그대로 부른다.
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import pgshim  # noqa: E402
from teamforming import ai, rules, store  # noqa: E402

DATA = ROOT / "tests" / "data"


class FakeHasher:  # bcrypt 대신(테스트 환경에 bcrypt 가 없음). 앱은 bcrypt 를 씀
    def hash(self, pw):
        return "sha$" + hashlib.sha256(pw.encode()).hexdigest()

    def verify(self, pw, h):
        return self.hash(pw) == h


H = FakeHasher()
FAILS: list[str] = []


def fresh_db() -> store.DB:
    admin = pgshim.Conn(dbname="postgres")
    admin.raw("drop database if exists tf_test")
    admin.raw("create database tf_test")
    db = store.DB(pgshim.Pool(dbname="tf_test"))
    store.ensure_schema_and_admin(db, "admin-pw", H)
    return db


def check(label, got, expected_first):
    ok = got.split(":")[0] == expected_first
    print(f"  {'OK ' if ok else 'FAIL'} {label:<48} 기대 {expected_first:<3} → {got}")
    if not ok:
        FAILS.append(f"{label}: {got}")


def no_of(state, name_prefix):
    for s in state.students.values():
        if s.name.startswith(name_prefix + "("):
            return s.student_no
    raise KeyError(name_prefix)


def run_steps(db, steps):
    st = store.load_state(db)
    for label, kind, *args, exp in steps:
        if kind == "apply":
            who, team = args
            got = store.apply(db, no_of(st, who), team)
        elif kind == "vote":
            voter, applicant, yes = args
            got = store.vote(db, no_of(st, voter), no_of(st, applicant), yes)
        elif kind == "close":
            got = store.close_recruiting(db)
        check(label, got, exp)


def assign_and_confirm(db, caller, label):
    st = store.load_state(db)
    out = ai.run_assignment(st.members, st.team_names, st.unassigned(), list(ai.DEFAULT_MODELS),
                            "test-key", progress=lambda t: None,
                            log=lambda *a: store.log_ai(db, *a), caller=caller)
    store.save_proposal(db, out.proposal, out.status_text)
    print("  배정 진행 안내:\n    " + out.status_text.replace("\n", "\n    "))
    check(f"{label} AI 배정", out.message, "승인")
    check(f"{label} 확정", store.confirm_proposal(db), "승인")
    st = store.load_state(db)
    problems = [p for tid in st.team_names for p in rules.team_problems(st.team_names[tid], st.members[tid])]
    if st.unassigned() or problems:
        FAILS.append(f"{label} 최종 구성: 남은 학생 {len(st.unassigned())}, 문제 {problems}")
        print("  FAIL 최종 구성", problems)
    else:
        print("  OK  최종 구성: 남은 학생 없음, 모든 팀이 규칙을 지킴")
    for tid in st.team_names:
        print(f"     {st.team_names[tid]}: " + ", ".join(f"{s.name}" for s in st.members[tid]))


# ------------------------------------------------ 가짜 모델들
def caller_valid(model, prompt, key, tick):
    """프롬프트의 데이터를 읽어 규칙을 지키는 답을 내는 '잘 하는 모델'."""
    data = json.loads(prompt.split("데이터:\n", 1)[1])
    teams = {t["team"]: [rules.Student(i * 100 + j, m["student_no"], m["name"], m["main_role"], tuple(m["sub_roles"]))
                         for j, m in enumerate(t["members"])] for i, t in enumerate(data["teams"])}
    un = [rules.Student(9000 + k, m["student_no"], m["name"], m["main_role"], tuple(m["sub_roles"]))
          for k, m in enumerate(data["unassigned_students"])]
    sol = rules.solve_assignment(teams, un)
    ans = {"assignments": [{"student_no": s.student_no, "team": t} for t, ss in sol.items() for s in ss]}
    return "```json\n" + json.dumps(ans, ensure_ascii=False) + "\n```", "", 1.0


def caller_bad(model, prompt, key, tick):
    """모두 팀1에 넣는 '틀리는 모델'."""
    data = json.loads(prompt.split("데이터:\n", 1)[1])
    ans = {"assignments": [{"student_no": m["student_no"], "team": 1} for m in data["unassigned_students"]]}
    return json.dumps(ans), "", 2.0


def caller_timeout(model, prompt, key, tick):
    return None, "120초 안에 응답하지 않아 끊음", 120.0


def caller_sequence(*callers):
    it = iter(callers)
    return lambda *a: next(it)(*a)


# ------------------------------------------------ 시나리오
def easy():
    print("\n[부록 1. 쉬움] 팀 3개")
    db = fresh_db()
    check("명단 올리기", store.start_from_roster(db, (DATA / "쉬움.csv").read_bytes(), 3, H), "승인")
    run_steps(db, [
        ("1 학생1→팀1", "apply", "학생1", 1, "승인"),
        ("1 학생2→팀2", "apply", "학생2", 2, "승인"),
        ("1 학생3→팀3", "apply", "학생3", 3, "승인"),
        ("2 학생4→팀1", "apply", "학생4", 1, "대기"),
        ("2 학생1 찬성 학생4", "vote", "학생1", "학생4", True, "승인"),
        ("3 학생5→팀2", "apply", "학생5", 2, "대기"),
        ("3 학생2 찬성 학생5", "vote", "학생2", "학생5", True, "승인"),
        ("4 학생7→팀1", "apply", "학생7", 1, "대기"),
        ("4 학생1 찬성 학생7", "vote", "학생1", "학생7", True, "대기"),
        ("4 학생4 거부 학생7", "vote", "학생4", "학생7", False, "거절"),
        ("5 학생6→팀3", "apply", "학생6", 3, "대기"),
        ("5 학생3 찬성 학생6", "vote", "학생3", "학생6", True, "승인"),
        ("6 마감", "close", "승인"),
    ])
    assign_and_confirm(db, caller_valid, "6")


def medium():
    print("\n[부록 2. 중간] 팀 4개")
    db = fresh_db()
    check("명단 올리기", store.start_from_roster(db, (DATA / "중간.csv").read_bytes(), 4, H), "승인")
    run_steps(db, [
        ("1 학생1→팀1", "apply", "학생1", 1, "승인"),
        ("2 학생5→팀1", "apply", "학생5", 1, "대기"),
        ("2 학생1 찬성 학생5", "vote", "학생1", "학생5", True, "승인"),
        ("3 학생2→팀1 (주 역할 중복)", "apply", "학생2", 1, "막힘"),
        ("4 학생6→팀2", "apply", "학생6", 2, "승인"),
        ("5 학생9→팀2", "apply", "학생9", 2, "대기"),
        ("5 학생6 찬성 학생9", "vote", "학생6", "학생9", True, "승인"),
        ("6 학생10→팀2", "apply", "학생10", 2, "대기"),
        ("6 학생6 찬성 학생10", "vote", "학생6", "학생10", True, "대기"),
        ("6 학생9 거부 학생10", "vote", "학생9", "학생10", False, "거절"),
        ("7 학생3→팀3", "apply", "학생3", 3, "승인"),
        ("7 학생11→팀4", "apply", "학생11", 4, "승인"),
        ("8 학생12→팀4", "apply", "학생12", 4, "대기"),
        ("8 학생12→팀3 (결과 기다리는 지원)", "apply", "학생12", 3, "막힘"),
        ("9 마감", "close", "승인"),
        ("10 학생2→팀3 (마감)", "apply", "학생2", 3, "막힘"),
    ])
    st = store.load_state(db)
    un = {s.name.split("(")[0] for s in st.unassigned()}
    ok = {"학생3", "학생11", "학생12"} <= un and not st.pending
    print(f"  {'OK ' if ok else 'FAIL'} 9 마감 뒤 학생12 거절·학생3·학생11 팀에서 빠짐 → 팀 없는 학생 {sorted(un)}")
    if not ok:
        FAILS.append("중간 9단계")
    # 1차 모델이 틀린 배정안 → 앱이 걸러 내고 2차 모델로
    assign_and_confirm(db, caller_sequence(caller_bad, caller_valid), "11")


def hard(ai_mode):
    print(f"\n[부록 3. 어려움] 팀 4개 (AI: {ai_mode})")
    db = fresh_db()
    check("명단 올리기", store.start_from_roster(db, (DATA / "어려움.csv").read_bytes(), 4, H), "승인")
    run_steps(db, [
        ("1 학생1→팀1", "apply", "학생1", 1, "승인"),
        ("2 학생4→팀1", "apply", "학생4", 1, "대기"),
        ("2 학생1 찬성 학생4", "vote", "학생1", "학생4", True, "승인"),
        ("3 학생7→팀1", "apply", "학생7", 1, "대기"),
        ("3 학생1 찬성 학생7", "vote", "학생1", "학생7", True, "대기"),
        ("3 학생4 찬성 학생7", "vote", "학생4", "학생7", True, "승인"),
        ("4 학생2→팀2", "apply", "학생2", 2, "승인"),
        ("5 학생8→팀2", "apply", "학생8", 2, "대기"),
        ("5 학생2 찬성 학생8", "vote", "학생2", "학생8", True, "승인"),
        ("6 학생5→팀2", "apply", "학생5", 2, "대기"),
        ("6 학생6→팀2", "apply", "학생6", 2, "대기"),
        ("7 학생2 찬성 학생5", "vote", "학생2", "학생5", True, "대기"),
        ("7 학생8 찬성 학생5", "vote", "학생8", "학생5", True, "승인"),
        ("8 학생2 찬성 학생6", "vote", "학생2", "학생6", True, "대기"),
        ("8 학생8 찬성 학생6", "vote", "학생8", "학생6", True, "대기"),
        ("8 학생5 찬성 학생6 (승인 직전 검사)", "vote", "학생5", "학생6", True, "거절"),
        ("9 학생11→팀3", "apply", "학생11", 3, "승인"),
        ("10 학생15→팀3", "apply", "학생15", 3, "대기"),
        ("10 학생11 찬성 학생15", "vote", "학생11", "학생15", True, "승인"),
        ("11 학생14→팀3 (필수 포함 역할 자리)", "apply", "학생14", 3, "막힘"),
        ("12 학생10→팀4", "apply", "학생10", 4, "승인"),
        ("13 학생13→팀1", "apply", "학생13", 1, "대기"),
        ("13 학생1 찬성 학생13", "vote", "학생1", "학생13", True, "대기"),
        ("14 마감", "close", "승인"),
    ])
    st = store.load_state(db)
    un = sorted(s.name.split("(")[0] for s in st.unassigned())
    ok = "학생13" in un and "학생10" in un and not st.pending
    print(f"  {'OK ' if ok else 'FAIL'} 14 마감 뒤 학생13 거절·학생10 빠짐 → 팀 없는 학생 {un}")
    if not ok:
        FAILS.append("어려움 14단계")
    caller = {"정상": caller_valid,
              "3차까지 실패": caller_sequence(caller_timeout, caller_bad, caller_timeout)}[ai_mode]
    assign_and_confirm(db, caller, "15")


def infeasible():
    print("\n[추가] 규칙을 지키는 배정이 없는 경우")
    db = fresh_db()
    csv = ("student_no,name,main_role,sub_roles\n"
           "9001,가상A,테스터,\n9002,가상B,발표자,\n9003,가상C,보고서 작성자,\n9004,가상D,프론트엔드 디자이너,\n"
           "9005,가상E,기획자,\n9006,가상F,웹 프로그래머,\n").encode()
    store.start_from_roster(db, csv, 2, H)
    # 앱의 지원 단계 검사 때문에 웹 프로그래머 없이 4명이 차는 팀은 만들 수 없음 → 확인
    st = store.load_state(db)
    n = {s.name: s.student_no for s in st.students.values()}
    check("A→팀1", store.apply(db, n["가상A"], 1), "승인")
    check("B→팀1", store.apply(db, n["가상B"], 1), "대기")
    check("A 찬성 B", store.vote(db, n["가상A"], n["가상B"], True), "승인")
    check("C→팀1 (자리 모자람)", store.apply(db, n["가상C"], 1), "막힘")
    # 배정 단계 안내 확인: 팀이 없는 학생이 모자라는 상황을 직접 만들어 검사
    teams = {1: [st.students[i] for i in st.students if st.students[i].name in ("가상A", "가상B", "가상C", "가상D")],
             2: []}
    un = [s for s in st.students.values() if s.name in ("가상E", "가상F")]
    out = ai.run_assignment(teams, {1: "팀1", 2: "팀2"}, un, ["m1"], "k", lambda t: None, lambda *a: None,
                            caller=lambda *a: (_ for _ in ()).throw(AssertionError("AI를 부르면 안 됨")))
    check("배정 없음 안내", out.message, "막힘")
    print("    " + out.status_text.replace("\n", "\n    "))


def timeout_mechanics():
    print("\n[추가] 모델 하나의 전체 대기 시간 제한(실제 스레드, 제한 2초로 축소)")
    orig = ai._request

    def slow(model, prompt, key, box):
        time.sleep(5)
        box["content"] = "늦은 응답"

    ai._request = slow
    ticks = []
    t0 = time.monotonic()
    content, reason, elapsed = ai.call_model("m", "p", "k", ticks.append, timeout=2)
    ai._request = orig
    ok = content is None and 1.9 < time.monotonic() - t0 < 3 and ticks
    print(f"  {'OK ' if ok else 'FAIL'} {reason} (경과 {elapsed:.1f}초, 진행 안내 {len(ticks)}번 갱신)")
    if not ok:
        FAILS.append("timeout")

    def empty(model, prompt, key, box):
        box["content"] = "   "
    ai._request = empty
    content, reason, _ = ai.call_model("m", "p", "k", lambda s: None, timeout=2)
    ai._request = orig
    print(f"  {'OK ' if reason == '빈 응답' else 'FAIL'} 빈 응답은 실패로 처리 → {reason}")


def misc():
    print("\n[추가] 로그인·명단 형식")
    db = fresh_db()
    check("BOM 있는 명단", store.start_from_roster(db, (DATA / "어려움.csv").read_bytes(), 4, H), "승인")
    ok = store.login(db, "2026301", "1234", H) and not store.login(db, "2026301", "12345", H) \
        and store.login(db, "admin", "admin-pw", H)["is_admin"]
    print(f"  {'OK ' if ok else 'FAIL'} 학생 1234 로그인, 틀린 비밀번호 거부, 명단을 다시 올려도 관리자 계정 남음")
    with db.tx() as c:
        h = store.one(c, "select password_hash from tf_students where student_no='2026301'")["password_hash"]
    print(f"  {'OK ' if h != '1234' else 'FAIL'} 비밀번호는 해시로 저장")
    check("열이 3개인 줄", store.start_from_roster(db, "student_no,name,main_role,sub_roles\n1,가,기획자\n".encode(), 2, H), "막힘")
    check("없는 역할", store.start_from_roster(db, "student_no,name,main_role,sub_roles\n1,가,요리사,\n".encode(), 2, H), "막힘")
    check("배정 전 확정", store.confirm_proposal(db), "막힘")


if __name__ == "__main__":
    easy()
    medium()
    hard("정상")
    hard("3차까지 실패")
    infeasible()
    timeout_mechanics()
    misc()
    print("\n" + ("모두 통과" if not FAILS else f"실패 {len(FAILS)}건:\n" + "\n".join(FAILS)))
    sys.exit(1 if FAILS else 0)

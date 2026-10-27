"""DB 읽기·쓰기와 지원/투표/마감/배정 처리.

모든 함수는 db.tx() 로 트랜잭션 하나 안에서 일한다.
같은 팀에 대한 지원·투표는 tf_teams 행을 FOR UPDATE 로 잠가 차례로 처리하고,
마지막 자리 경쟁은 tf_members 의 유일성 제약이 최종으로 막는다.
결과 안내는 항상 "승인", "대기", "거절", "막힘" 가운데 하나로 시작한다.
"""
from __future__ import annotations

import csv
import io
import json
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from . import rules
from .rules import Student

try:  # psycopg 3
    from psycopg.rows import dict_row
except ImportError:  # 테스트용 대체 드라이버
    dict_row = None

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema.sql"
STUDENT_PASSWORD = "1234"


# ---------------------------------------------------------------- DB 연결
class DB:
    """연결 풀을 감싼다. 연결은 앱이 시작할 때 한 번 만든 풀에서 빌려 쓴다(4.2절)."""

    def __init__(self, pool):
        self.pool = pool

    @contextmanager
    def tx(self):
        with self.pool.connection() as conn:  # 끝나면 commit, 예외면 rollback
            yield conn


def rows(conn, sql: str, params=()) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else []


def one(conn, sql: str, params=()) -> dict | None:
    r = rows(conn, sql, params)
    return r[0] if r else None


def run(conn, sql: str, params=()) -> int:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.rowcount


def is_unique_violation(e: Exception) -> bool:
    return getattr(e, "sqlstate", None) == "23505"


# ---------------------------------------------------------------- 설정 값
def get_setting(conn, key: str, default: str | None = None) -> str | None:
    r = one(conn, "select value from tf_settings where key = %s", (key,))
    return r["value"] if r else default


def set_setting(conn, key: str, value: str | None) -> None:
    run(
        conn,
        "insert into tf_settings(key, value) values (%s, %s) "
        "on conflict (key) do update set value = excluded.value",
        (key, value),
    )


# ---------------------------------------------------------------- 시작 준비
def ensure_schema_and_admin(db: DB, admin_password: str, hasher) -> None:
    """schema.sql 을 실행하고 관리자 계정을 만들거나 비밀번호를 갱신한다."""
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    with db.tx() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
        for key, val in (("closed", "false"), ("confirmed", "false")):
            run(conn, "insert into tf_settings(key, value) values (%s, %s) on conflict do nothing", (key, val))
        admin = one(conn, "select id, password_hash from tf_students where student_no = 'admin'")
        if admin is None:
            run(
                conn,
                "insert into tf_students(student_no, name, main_role, password_hash, is_admin) "
                "values ('admin', '관리자', null, %s, true)",
                (hasher.hash(admin_password),),
            )
        elif not hasher.verify(admin_password, admin["password_hash"]):
            run(conn, "update tf_students set password_hash = %s where id = %s",
                (hasher.hash(admin_password), admin["id"]))


def login(db: DB, student_no: str, password: str, hasher) -> dict | None:
    with db.tx() as conn:
        r = one(conn, "select id, student_no, name, password_hash, is_admin from tf_students where student_no = %s",
                (student_no.strip(),))
    if r and password and hasher.verify(password, r["password_hash"]):
        return {"student_no": r["student_no"], "name": r["name"], "is_admin": r["is_admin"]}
    return None


# ---------------------------------------------------------------- 명단 올리기
class RosterError(ValueError):
    pass


def parse_roster(data: bytes) -> list[dict]:
    """BOM 이 있어도 읽도록 utf-8-sig 로 읽는다(2.6절)."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise RosterError("CSV를 UTF-8로 읽을 수 없음")
    reader = csv.reader(io.StringIO(text))
    lines = [r for r in reader if any(c.strip() for c in r)]
    if not lines:
        raise RosterError("CSV가 비어 있음")
    header = [h.strip() for h in lines[0]]
    if header != ["student_no", "name", "main_role", "sub_roles"]:
        raise RosterError("첫 줄이 student_no,name,main_role,sub_roles 가 아님")
    out, seen = [], set()
    for i, r in enumerate(lines[1:], start=2):
        if len(r) != 4:
            raise RosterError(f"{i}번째 줄의 열이 4개가 아님")
        no, name, main, subs = (c.strip() for c in r)
        if not no or not name:
            raise RosterError(f"{i}번째 줄에 학번이나 이름이 없음")
        if no == "admin":
            raise RosterError("학번 admin 은 관리자용이라 쓸 수 없음")
        if no in seen:
            raise RosterError(f"학번 {no}가 두 번 나옴")
        if main not in rules.ROLES:
            raise RosterError(f"{i}번째 줄의 주 역할 '{main}'이(가) 6개 역할에 없음")
        sub_list = [s.strip() for s in subs.split(";") if s.strip()] if subs else []
        if len(sub_list) > 2:
            raise RosterError(f"{i}번째 줄의 보조 역할이 2개를 넘음")
        for s in sub_list:
            if s not in rules.ROLES:
                raise RosterError(f"{i}번째 줄의 보조 역할 '{s}'이(가) 6개 역할에 없음")
        seen.add(no)
        out.append({"student_no": no, "name": name, "main_role": main,
                    "sub_roles": [s for s in dict.fromkeys(sub_list) if s != main]})
    return out


def start_from_roster(db: DB, data: bytes | None, team_count, hasher) -> str:
    if not data:
        return "막힘: 명단 CSV 파일을 먼저 올려야 함"
    try:
        team_count = int(team_count)
    except (TypeError, ValueError):
        return "막힘: 팀 수가 숫자가 아님"
    if not 1 <= team_count <= 20:
        return "막힘: 팀 수는 1~20 사이여야 함"
    try:
        roster = parse_roster(data)
    except RosterError as e:
        return f"막힘: {e}"
    if not roster:
        return "막힘: 명단에 학생이 없음"
    pw_hashes = [hasher.hash(STUDENT_PASSWORD) for _ in roster]
    with db.tx() as conn:
        run(conn, "lock table tf_settings in exclusive mode")
        run(conn, "delete from tf_votes")
        run(conn, "delete from tf_applications")
        run(conn, "delete from tf_members")
        run(conn, "delete from tf_teams")
        run(conn, "delete from tf_students where not is_admin")  # 관리자 계정은 남김
        run(conn, "delete from tf_settings where key not in ('keepalive')")
        for k, v in (("closed", "false"), ("confirmed", "false")):
            set_setting(conn, k, v)
        for n in range(1, team_count + 1):
            run(conn, "insert into tf_teams(id, name) values (%s, %s)", (n, f"팀{n}"))
        for s, h in zip(roster, pw_hashes):
            r = one(conn,
                    "insert into tf_students(student_no, name, main_role, password_hash) "
                    "values (%s, %s, %s, %s) returning id",
                    (s["student_no"], s["name"], s["main_role"], h))
            for role in s["sub_roles"]:
                run(conn, "insert into tf_student_sub_roles(student_id, role) values (%s, %s)", (r["id"], role))
    return f"승인: 명단으로 처음 상태를 만듦(학생 {len(roster)}명, 팀 {team_count}개)"


# ---------------------------------------------------------------- 상태 읽기
@dataclass
class Application:
    id: int
    team_id: int
    student: Student
    status: str
    votes: dict[int, str] = field(default_factory=dict)  # voter_id → 찬성/거부


@dataclass
class State:
    students: dict[int, Student]
    team_names: dict[int, str]
    members: dict[int, list[Student]]          # team_id → 자리 순서대로
    team_of: dict[int, int]                    # student_id → team_id
    pending: list[Application]
    closed: bool
    confirmed: bool
    proposal: dict | None
    assign_status: str | None

    def by_no(self, student_no: str) -> Student | None:
        for s in self.students.values():
            if s.student_no == student_no:
                return s
        return None

    def unassigned(self) -> list[Student]:
        return sorted((s for s in self.students.values() if s.id not in self.team_of),
                      key=lambda s: s.student_no)

    def pending_of(self, student_id: int) -> Application | None:
        return next((a for a in self.pending if a.student.id == student_id), None)


def _students(conn) -> dict[int, Student]:
    out = {}
    for r in rows(conn,
                  "select s.id, s.student_no, s.name, s.main_role, "
                  "coalesce(string_agg(r.role, ';' order by r.role), '') as subs "
                  "from tf_students s left join tf_student_sub_roles r on r.student_id = s.id "
                  "where not s.is_admin group by s.id order by s.student_no"):
        subs = tuple(x for x in r["subs"].split(";") if x)
        out[r["id"]] = Student(r["id"], r["student_no"], r["name"], r["main_role"], subs)
    return out


def _members(conn, students) -> dict[int, list[Student]]:
    mem: dict[int, list[Student]] = {}
    for r in rows(conn, "select team_id, student_id from tf_members order by team_id, slot_no"):
        mem.setdefault(r["team_id"], []).append(students[r["student_id"]])
    return mem


def load_state(db: DB) -> State:
    with db.tx() as conn:
        students = _students(conn)
        team_names = {r["id"]: r["name"] for r in rows(conn, "select id, name from tf_teams order by id")}
        members = {tid: [] for tid in team_names}
        members.update(_members(conn, students))
        pend = rows(conn, "select id, team_id, student_id, status from tf_applications "
                          "where status = '대기' order by id")
        votes = rows(conn, "select v.application_id, v.voter_id, v.vote from tf_votes v "
                           "join tf_applications a on a.id = v.application_id where a.status = '대기'")
        settings = {r["key"]: r["value"] for r in rows(conn, "select key, value from tf_settings")}
    apps = [Application(r["id"], r["team_id"], students[r["student_id"]], r["status"]) for r in pend]
    by_id = {a.id: a for a in apps}
    for v in votes:
        if v["application_id"] in by_id:
            by_id[v["application_id"]].votes[v["voter_id"]] = v["vote"]
    team_of = {s.id: tid for tid, ms in members.items() for s in ms}
    proposal = json.loads(settings["proposal"]) if settings.get("proposal") else None
    return State(students, team_names, members, team_of, apps,
                 settings.get("closed") == "true", settings.get("confirmed") == "true",
                 proposal, settings.get("assign_status"))


# ---------------------------------------------------------------- 지원
def apply(db: DB, student_no: str, team_id: int) -> str:
    with db.tx() as conn:
        # 잠그는 순서: tf_settings(closed) → tf_teams. 마감과 지원이 엇갈리지 않게 함
        closed = one(conn, "select value from tf_settings where key = 'closed' for share")
        if closed and closed["value"] == "true":
            return "막힘: 모집이 마감됨"
        team = one(conn, "select id, name from tf_teams where id = %s for update", (team_id,))
        if team is None:
            return "막힘: 없는 팀임"
        students = _students(conn)
        me = next((s for s in students.values() if s.student_no == student_no), None)
        if me is None:
            return "막힘: 학생 계정이 아님"
        if one(conn, "select 1 from tf_members where student_id = %s", (me.id,)):
            return "막힘: 이미 팀이 있음"
        if one(conn, "select 1 from tf_applications where student_id = %s and status = '대기'", (me.id,)):
            return "막힘: 결과를 기다리는 지원이 있음"
        members = _members(conn, students).get(team_id, [])
        reason = rules.join_block_reason(members, me)
        if reason:
            return f"막힘: {reason}"
        if not members:
            # 빈 팀: 투표할 팀원이 없으므로 바로 승인
            run(conn, "insert into tf_members(team_id, slot_no, student_id) values (%s, 1, %s)", (team_id, me.id))
            run(conn, "insert into tf_applications(team_id, student_id, status, reason, decided_at) "
                      "values (%s, %s, '승인', '빈 팀', now())", (team_id, me.id))
            return f"승인: {team['name']}에 들어감(빈 팀이라 바로 승인)"
        run(conn, "insert into tf_applications(team_id, student_id, status) values (%s, %s, '대기')",
            (team_id, me.id))
        return f"대기: {team['name']} 팀원 {len(members)}명의 투표를 기다리는 중"


# ---------------------------------------------------------------- 투표
def vote(db: DB, voter_no: str, applicant_no: str, yes: bool) -> str:
    with db.tx() as conn:
        closed = one(conn, "select value from tf_settings where key = 'closed' for share")
        students = _students(conn)
        voter = next((s for s in students.values() if s.student_no == voter_no), None)
        applicant = next((s for s in students.values() if s.student_no == applicant_no), None)
        if voter is None or applicant is None:
            return "막힘: 학생을 찾을 수 없음"
        mrow = one(conn, "select team_id from tf_members where student_id = %s", (voter.id,))
        if mrow is None:
            return "막힘: 팀원만 투표할 수 있음"
        team_id = mrow["team_id"]
        team = one(conn, "select id, name from tf_teams where id = %s for update", (team_id,))
        app = one(conn, "select id, status from tf_applications where team_id = %s and student_id = %s "
                        "order by id desc limit 1", (team_id, applicant.id))
        if app is None:
            return "막힘: 우리 팀에 들어온 그 학생의 지원이 없음"
        if app["status"] != "대기":
            return f"{app['status']}: 이미 결과가 나온 지원임"
        if closed and closed["value"] == "true":
            return "막힘: 모집이 마감되어 투표할 수 없음"
        if one(conn, "select 1 from tf_votes where application_id = %s and voter_id = %s", (app["id"], voter.id)):
            return "대기: 이미 투표했음(투표는 바꿀 수 없음)"
        run(conn, "insert into tf_votes(application_id, voter_id, vote) values (%s, %s, %s)",
            (app["id"], voter.id, "찬성" if yes else "거부"))
        if not yes:
            # 한 명이라도 거부하면 다른 팀원의 투표를 기다리지 않고 바로 거절
            run(conn, "update tf_applications set status = '거절', reason = %s, decided_at = now() where id = %s",
                (f"{voter.name}이(가) 거부함", app["id"]))
            return f"거절: 팀원이 거부함({applicant.name}의 {team['name']} 지원)"

        members = _members(conn, students).get(team_id, [])
        yes_ids = {r["voter_id"] for r in rows(conn, "select voter_id from tf_votes where application_id = %s "
                                                     "and vote = '찬성'", (app["id"],))}
        waiting = [m for m in members if m.id not in yes_ids]  # 투표하는 때의 팀원 기준
        if waiting:
            return f"대기: 남은 투표 {len(waiting)}명({', '.join(m.name for m in waiting)})"

        # 모든 팀원이 찬성 → 승인 직전 재검사(3장 어려운 해결)
        reason = rules.join_block_reason(members, applicant)
        if reason:
            run(conn, "update tf_applications set status = '거절', reason = %s, decided_at = now() where id = %s",
                (f"승인 직전 검사: {reason}", app["id"]))
            return f"거절: 승인 직전 검사에서 걸림 - {reason}"
        used = {r["slot_no"] for r in rows(conn, "select slot_no from tf_members where team_id = %s", (team_id,))}
        slot = min(set(range(1, rules.MAX_SIZE + 1)) - used)
        try:
            run(conn, "insert into tf_members(team_id, slot_no, student_id) values (%s, %s, %s)",
                (team_id, slot, applicant.id))
        except Exception as e:  # 유일성 제약: 먼저 도착한 학생만 기록됨
            if not is_unique_violation(e):
                raise
            conn.rollback()
            run(conn, "update tf_applications set status = '거절', reason = '자리 경쟁에서 밀림', "
                      "decided_at = now() where id = %s and status = '대기'", (app["id"],))
            return "거절: 다른 학생이 먼저 그 자리에 들어감"
        run(conn, "update tf_applications set status = '승인', reason = '모든 팀원 찬성', decided_at = now() "
                  "where id = %s", (app["id"],))
        return f"승인: {applicant.name}이(가) {team['name']}의 팀원이 됨"


# ---------------------------------------------------------------- 마감
def close_recruiting(db: DB) -> str:
    with db.tx() as conn:
        row = one(conn, "select value from tf_settings where key = 'closed' for update")
        if row and row["value"] == "true":
            return "막힘: 이미 모집을 마감했음"
        if not rows(conn, "select id from tf_teams"):
            return "막힘: 명단을 먼저 올려야 함"
        rows(conn, "select id from tf_teams order by id for update")
        set_setting(conn, "closed", "true")
        n_rej = run(conn, "update tf_applications set status = '거절', reason = '모집 마감', decided_at = now() "
                          "where status = '대기'")
        n_out = run(conn, "delete from tf_members where team_id in "
                          "(select team_id from tf_members group by team_id having count(*) = 1)")
        set_setting(conn, "proposal", None)
        set_setting(conn, "assign_status", None)
    return f"승인: 모집을 마감함(대기 중이던 지원 {n_rej}건 거절, 1명뿐인 팀에서 {n_out}명 빠짐)"


# ---------------------------------------------------------------- 배정안 저장·확정
def save_proposal(db: DB, proposal: dict | None, status_text: str | None) -> None:
    with db.tx() as conn:
        set_setting(conn, "proposal", json.dumps(proposal, ensure_ascii=False) if proposal else None)
        set_setting(conn, "assign_status", status_text)


def save_assign_status(db: DB, status_text: str | None) -> None:
    with db.tx() as conn:
        set_setting(conn, "assign_status", status_text)


def log_ai(db: DB, run_id: str, attempt: int, model: str, outcome: str, elapsed: float, detail: str) -> None:
    with db.tx() as conn:
        run(conn, "insert into tf_ai_logs(run_id, attempt, model, outcome, elapsed_sec, detail) "
                  "values (%s, %s, %s, %s, %s, %s)", (run_id, attempt, model, outcome, round(elapsed, 1), detail[:2000]))


def recent_ai_logs(db: DB, limit: int = 12) -> list[dict]:
    with db.tx() as conn:
        return rows(conn, "select run_id, attempt, model, outcome, elapsed_sec, detail, created_at "
                          "from tf_ai_logs order by id desc limit %s", (limit,))


def confirm_proposal(db: DB) -> str:
    with db.tx() as conn:
        row = one(conn, "select value from tf_settings where key = 'closed' for update")
        if not row or row["value"] != "true":
            return "막힘: 모집을 마감한 뒤에 확정할 수 있음"
        if get_setting(conn, "confirmed") == "true":
            return "막힘: 이미 배정을 확정했음"
        raw = get_setting(conn, "proposal")
        if not raw:
            return "막힘: 확정할 배정안이 없음(먼저 AI 배정을 눌러야 함)"
        proposal = json.loads(raw)
        rows(conn, "select id from tf_teams order by id for update")
        students = _students(conn)
        team_names = {r["id"]: r["name"] for r in rows(conn, "select id, name from tf_teams")}
        members = {tid: [] for tid in team_names}
        members.update(_members(conn, students))
        in_team = {s.id for ms in members.values() for s in ms}
        unassigned = [s for s in students.values() if s.id not in in_team]
        pairs = [(it["student_no"], it["team_id"]) for it in proposal["items"]]
        errors, after = rules.validate_assignment(members, team_names, unassigned, pairs)
        if errors:  # 배정안을 만든 뒤 상태가 바뀌었으면 확정하지 않음
            return "막힘: 배정안이 지금 상태와 맞지 않음 - " + "; ".join(errors[:3])
        for tid in sorted(after):
            used = {s.id for s in members[tid]}
            slot = len(members[tid])
            for s in after[tid]:
                if s.id in used:
                    continue
                slot += 1
                run(conn, "insert into tf_members(team_id, slot_no, student_id) values (%s, %s, %s)",
                    (tid, slot, s.id))
        set_setting(conn, "confirmed", "true")
        set_setting(conn, "proposal", None)
    return f"승인: 배정을 확정함({len(pairs)}명 배정, 모든 팀이 규칙을 지킴)"

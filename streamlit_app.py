"""팀 프로젝트 팀 편성 앱 (Streamlit).

화면 요소의 key 는 공고 2.7절 표를 그대로 따른다.
키(오픈라우터 키, DB 주소, 관리자 비밀번호)는 화면이나 로그에 절대 출력하지 않는다.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from zoneinfo import ZoneInfo

import bcrypt
import streamlit as st
from dotenv import load_dotenv

from teamforming import ai, store
from teamforming.rules import REQUIRED_ROLES

load_dotenv(Path(__file__).resolve().parent / ".env")
KST = ZoneInfo("Asia/Seoul")
AUTO_REFRESH_SEC = 3  # 다른 사람의 변경을 3초마다 다시 표시(5초 이하, 2.7절)

st.set_page_config(page_title="팀 편성", page_icon="🧩", layout="centered")


# ---------------------------------------------------------------- 비밀 값
def secret(name: str) -> str | None:
    """.env(로컬) → Streamlit Secrets(배포) 순서로 찾는다. 값은 절대 출력하지 않는다."""
    v = os.environ.get(name)
    if v:
        return v
    try:
        v = st.secrets.get(name)
    except Exception:  # secrets.toml 이 없을 때
        v = None
    return str(v) if v else None


class BcryptHasher:
    def hash(self, pw: str) -> str:
        return bcrypt.hashpw(pw.encode(), bcrypt.gensalt(rounds=10)).decode()

    def verify(self, pw: str, hashed: str) -> bool:
        try:
            return bcrypt.checkpw(pw.encode(), hashed.encode())
        except ValueError:
            return False


HASHER = BcryptHasher()


@st.cache_resource(show_spinner="DB에 연결하는 중…")
def get_db() -> store.DB:
    """연결 풀은 앱 전체에서 한 번만 만들고 다시 쓴다(4.2절)."""
    from psycopg_pool import ConnectionPool

    pool = ConnectionPool(
        secret("DATABASE_URL"),
        min_size=1,
        max_size=5,
        # Supabase 풀러(트랜잭션 모드)에서는 준비된 문장을 쓰지 않아야 함
        kwargs={"prepare_threshold": None, "connect_timeout": 10},
        check=ConnectionPool.check_connection,
        open=True,
    )
    pool.wait(timeout=30)
    return store.DB(pool)


@st.cache_resource(show_spinner="처음 준비 중…")
def prepare(_db: store.DB, admin_fingerprint: str) -> bool:
    """테이블을 만들고 관리자 계정을 만들거나 비밀번호를 갱신한다(앱 시작 때 한 번)."""
    store.ensure_schema_and_admin(_db, secret("ADMIN_PASSWORD"), HASHER)
    return True


def models() -> list[str]:
    raw = secret("AI_MODELS")
    if raw:
        lst = [m.strip() for m in raw.split(",") if m.strip()]
        if lst:
            return lst[:3]
    return list(ai.DEFAULT_MODELS)


# ---------------------------------------------------------------- 공통 화면 조각
def set_result(msg: str) -> None:
    st.session_state["result_msg"] = msg


def show_result() -> None:
    """단추를 누른 결과 안내. 항상 승인/대기/거절/막힘 가운데 하나로 시작한다."""
    msg = st.session_state.get("result_msg")
    with st.container(key="result"):
        if not msg:
            st.caption("단추를 누르면 결과가 여기에 나옴")
        elif msg.startswith("승인"):
            st.success(msg)
        elif msg.startswith("대기"):
            st.info(msg)
        else:
            st.error(msg)


def safe(fn, *args) -> str:
    try:
        return fn(*args)
    except Exception as e:  # noqa: BLE001 - 화면에 키나 DB 주소가 나오지 않게 종류만 보여 줌
        if store.is_unique_violation(e):
            return "막힘: 다른 요청이 먼저 처리되어 받아들일 수 없음(결과를 기다리는 지원이 있거나 자리가 참)"
        return f"막힘: 처리 중 오류가 났음({type(e).__name__}). 잠시 뒤 다시 시도해야 함"


def member_line(s) -> str:
    subs = ", ".join(s.sub_roles) if s.sub_roles else "없음"
    return f"**{s.name}** · 주 역할 {s.main_role} · 보조 {subs}"


def team_card(state: store.State, tid: int, my_team: int | None):
    ms = state.members.get(tid, [])
    with st.container(key=f"team_{tid}", border=True):
        mark = " · 🟢 **내 팀**" if tid == my_team else ""
        st.markdown(f"#### {state.team_names[tid]} <small>({len(ms)}/4명)</small>{mark}", unsafe_allow_html=True)
        if ms:
            for s in ms:
                st.markdown("- " + member_line(s))
            have = [r for r in REQUIRED_ROLES if any(m.can(r) for m in ms)]
            st.caption("필수 포함 역할 가능: " + (", ".join(have) if have else "아직 없음"))
        else:
            st.caption("팀원 없음 (지원하면 바로 승인)")
        waiting = [a.student.name for a in state.pending if a.team_id == tid]
        if waiting:
            st.caption("투표를 기다리는 지원: " + ", ".join(waiting))
    return ms


# ---------------------------------------------------------------- 로그인
def login_view(db: store.DB) -> None:
    st.title("🧩 팀 프로젝트 팀 편성")
    st.caption("학번과 비밀번호로 로그인함. 관리자는 학번 admin")
    st.text_input("학번", key="login_student_no")
    st.text_input("비밀번호", type="password", key="login_password")
    if st.button("로그인", key="login_submit", type="primary"):
        user = store.login(db, st.session_state.get("login_student_no", ""),
                           st.session_state.get("login_password", ""), HASHER)
        if user:
            st.session_state["user"] = user
            set_result(f"승인: {user['name']}(으)로 로그인함")
            st.rerun()
        set_result("막힘: 학번 또는 비밀번호가 틀림")
    show_result()


def header(user: dict) -> None:
    c1, c2 = st.columns([4, 1])
    with c1:
        with st.container(key="whoami"):
            role = "관리자" if user["is_admin"] else "학생"
            st.markdown(f"👤 **{user['name']}** ({user['student_no']}, {role})")
    with c2:
        if st.button("로그아웃", key="logout"):
            st.session_state.clear()
            st.rerun()


# ---------------------------------------------------------------- 학생 화면
def do_apply(db, student_no, tid):
    set_result(safe(store.apply, db, student_no, tid))


def do_vote(db, voter_no, applicant_no, yes):
    set_result(safe(store.vote, db, voter_no, applicant_no, yes))


@st.fragment(run_every=AUTO_REFRESH_SEC)
def student_panel(db: store.DB, student_no: str) -> None:
    state = store.load_state(db)
    me = state.by_no(student_no)
    show_result()
    if me is None:
        st.warning("명단이 새로 올라와 이 계정이 없어졌음. 로그아웃 뒤 다시 로그인해야 함")
        return

    my_team = state.team_of.get(me.id)
    my_app = state.pending_of(me.id)
    st.markdown(f"내 역할: {me.roles_text()}")
    if state.closed:
        st.warning("모집이 마감되었음. 지원과 투표를 할 수 없음")
    if my_team:
        st.success(f"내 팀: {state.team_names[my_team]}")
    elif my_app:
        need = len(state.members.get(my_app.team_id, []))
        yes = sum(1 for v in my_app.votes.values() if v == "찬성")
        st.info(f"{state.team_names[my_app.team_id]} 지원: 대기 (찬성 {yes}/{need})")
    else:
        st.info("아직 팀이 없음")

    # 내 팀에 들어온 지원에 투표
    if my_team:
        apps = [a for a in state.pending if a.team_id == my_team]
        st.subheader("내 팀에 들어온 지원")
        if not apps:
            st.caption("투표할 지원이 없음")
        for a in apps:
            with st.container(border=True):
                st.markdown(member_line(a.student))
                mine = a.votes.get(me.id)
                if mine:
                    st.markdown(f"내 투표: **{mine}**")
                else:
                    c1, c2 = st.columns(2)
                    no = a.student.student_no
                    c1.button("찬성", key=f"vote_yes_{no}", disabled=state.closed,
                              on_click=do_vote, args=(db, student_no, no, True))
                    c2.button("거부", key=f"vote_no_{no}", disabled=state.closed,
                              on_click=do_vote, args=(db, student_no, no, False))

    st.subheader("팀 목록")
    # 이미 팀이 있음, 결과를 기다리는 지원이 있음, 모집 마감 → 지원 단추 비활성(2.8절)
    can_apply = my_team is None and my_app is None and not state.closed
    for tid in state.team_names:
        team_card(state, tid, my_team)
        st.button(f"{state.team_names[tid]}에 지원", key=f"apply_{tid}", disabled=not can_apply,
                  on_click=do_apply, args=(db, student_no, tid))


# ---------------------------------------------------------------- 관리자 화면
@st.fragment(run_every=AUTO_REFRESH_SEC)
def admin_live(db: store.DB) -> None:
    state = store.load_state(db)
    flags = [f"학생 {len(state.students)}명", f"팀 {len(state.team_names)}개",
             "모집 마감됨" if state.closed else "모집 중", "배정 확정됨" if state.confirmed else ""]
    st.caption(" · ".join(f for f in flags if f))
    with st.container(key="unassigned", border=True):
        un = state.unassigned()
        st.markdown(f"**팀이 없는 학생 ({len(un)}명)**")
        st.markdown(", ".join(s.name for s in un) if un else "없음")
    for tid in state.team_names:
        team_card(state, tid, None)


def admin_view(db: store.DB) -> None:
    show_result()

    st.subheader("1. 명단 올리기")
    up = st.file_uploader("명단 CSV (UTF-8, BOM 있어도 됨)", type=["csv"], key="admin_roster_file")
    team_count = st.number_input("팀 수", min_value=1, max_value=20, value=4, step=1, key="admin_team_count")
    if st.button("명단으로 처음 상태 만들기", key="admin_start"):
        set_result(safe(store.start_from_roster, db, up.getvalue() if up else None, team_count, HASHER))
        st.rerun()

    st.subheader("2. 모집 마감")
    if st.button("모집 마감", key="admin_close"):
        set_result(safe(store.close_recruiting, db))
        st.rerun()

    st.subheader("팀 현황")
    admin_live(db)

    st.subheader("3. AI 배정")
    st.caption("1차 → 2차 → 3차 모델: " + " → ".join(models()))
    clicked = st.button("AI 배정", key="admin_assign")
    status_box = st.container(key="assign_status", border=True)
    if clicked:
        set_result(run_assign(db, status_box))
        st.rerun()
    state = store.load_state(db)
    with status_box:
        st.markdown("**배정 진행 안내**")
        st.text(state.assign_status or "아직 배정을 하지 않았음")

    if state.proposal:
        p = state.proposal
        st.markdown(f"**배정안** (만든 곳: {p['source']})")
        if p["items"]:
            st.table([{"이름": it["name"], "주 역할": it["main_role"],
                       "보조 역할": ", ".join(it["sub_roles"]) or "없음", "배정 팀": it["team_name"]}
                      for it in p["items"]])
    if st.button("배정안 확정", key="admin_confirm", type="primary"):
        set_result(safe(store.confirm_proposal, db))
        st.rerun()

    with st.expander("AI 호출 기록 (최근 12건, 한국 시각)"):
        logs = store.recent_ai_logs(db)
        if logs:
            st.table([{"시각": r["created_at"].astimezone(KST).strftime("%m/%d %H:%M:%S")
                       if hasattr(r["created_at"], "astimezone") else str(r["created_at"]),
                       "실행": r["run_id"], "차수": r["attempt"], "모델": r["model"], "결과": r["outcome"],
                       "초": float(r["elapsed_sec"] or 0), "내용": (r["detail"] or "")[:160]} for r in logs])
        else:
            st.caption("기록 없음")


def run_assign(db: store.DB, status_box) -> str:
    state = store.load_state(db)
    if not state.team_names:
        return "막힘: 명단을 먼저 올려야 함"
    if not state.closed:
        return "막힘: 모집을 마감한 뒤에 배정할 수 있음"
    if state.confirmed:
        return "막힘: 이미 배정을 확정했음"
    with status_box:
        live = st.empty()

    def progress(text: str) -> None:
        live.text(text)

    def log(run_id, attempt, model, outcome, elapsed, detail):
        try:
            store.log_ai(db, run_id, attempt, model, outcome, elapsed, detail)
        except Exception:  # noqa: BLE001 - 기록 실패가 배정을 막지 않게
            pass

    progress("배정을 시작함…")
    try:
        out = ai.run_assignment(state.members, state.team_names, state.unassigned(), models(),
                                secret("OPENROUTER_API_KEY"), progress, log)
    except Exception as e:  # noqa: BLE001
        return f"막힘: 배정 중 오류가 났음({type(e).__name__})"
    store.save_proposal(db, out.proposal, out.status_text)
    return out.message


# ---------------------------------------------------------------- 시작
def main() -> None:
    missing = [k for k in ("DATABASE_URL", "ADMIN_PASSWORD") if not secret(k)]
    if missing:
        st.error("설정 값이 없음: " + ", ".join(missing) + " (.env 또는 Streamlit Secrets에 넣어야 함)")
        st.stop()
    try:
        db = get_db()
        prepare(db, hashlib.sha256(secret("ADMIN_PASSWORD").encode()).hexdigest())
    except Exception as e:  # noqa: BLE001 - DB 주소가 화면에 나오지 않게 종류만
        st.error(f"DB에 연결할 수 없음({type(e).__name__}). Supabase가 일시정지되었는지 확인해야 함")
        st.stop()

    user = st.session_state.get("user")
    if not user:
        login_view(db)
        return
    header(user)
    if user["is_admin"]:
        admin_view(db)
    else:
        student_panel(db, user["student_no"])


main()

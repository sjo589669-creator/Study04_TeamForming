"""테스트 전용: Streamlit 을 설치할 수 없는 환경에서 화면 코드가 오류 없이 도는지와
공고 2.7절 key 가 실제로 그려지는지 확인한다. 가짜 st 모듈로 streamlit_app.py 를 실행한다.
"""
from __future__ import annotations

import hashlib
import os
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
import pgshim  # noqa: E402

# ---- 가짜 bcrypt / psycopg_pool
bc = types.ModuleType("bcrypt")
bc.gensalt = lambda rounds=12: b"salt"
bc.hashpw = lambda pw, salt: b"sha$" + hashlib.sha256(pw).hexdigest().encode()
bc.checkpw = lambda pw, h: bc.hashpw(pw, b"") == h
sys.modules["bcrypt"] = bc
pp = types.ModuleType("psycopg_pool")


class FakePool(pgshim.Pool):
    check_connection = None

    def __init__(self, url, **kw):
        super().__init__(dbname="tf_test")

    def wait(self, timeout=0):
        pass


FakePool.check_connection = staticmethod(lambda c: None)
pp.ConnectionPool = FakePool
sys.modules["psycopg_pool"] = pp

# ---- 가짜 streamlit
KEYS: list[str] = []
CLICK: set[str] = set()
TEXT: list[str] = []


class Rerun(Exception):
    pass


class Stop(Exception):
    pass


class Ctx:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __getattr__(self, name):
        return getattr(st, name)


st = types.ModuleType("streamlit")
st.session_state = {}
st.secrets = {}


def _key(key):
    if key:
        assert key not in KEYS, f"key 중복: {key}"
        KEYS.append(key)


def _text(*a, **k):
    TEXT.append(" ".join(str(x) for x in a))
    return Ctx()


for name in ("markdown", "caption", "success", "info", "error", "warning", "subheader", "title", "text", "table"):
    setattr(st, name, _text)
st.set_page_config = lambda **k: None
st.cache_resource = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
st.fragment = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
st.rerun = lambda **k: (_ for _ in ()).throw(Rerun())
st.stop = lambda: (_ for _ in ()).throw(Stop())
st.empty = lambda: Ctx()
st.expander = lambda *a, **k: Ctx()


def container(key=None, border=False):
    _key(key)
    return Ctx()


def columns(n):
    return [Ctx() for _ in range(n if isinstance(n, int) else len(n))]


def button(label, key=None, disabled=False, on_click=None, args=(), type=None):
    _key(key)
    TEXT.append(f"[단추 {key}{' 비활성' if disabled else ''}]")
    if key in CLICK and not disabled:
        if on_click:
            on_click(*args)
            return False
        return True
    return False


def text_input(label, key=None, type=None):
    _key(key)
    return st.session_state.get(key, "")


def number_input(label, key=None, **k):
    _key(key)
    return st.session_state.get(key, k.get("value"))


class Upload:
    def __init__(self, b):
        self.b = b

    def getvalue(self):
        return self.b


def file_uploader(label, type=None, key=None):
    _key(key)
    return st.session_state.get(key)


st.container, st.columns, st.button = container, columns, button
st.text_input, st.number_input, st.file_uploader = text_input, number_input, file_uploader
sys.modules["streamlit"] = st

os.environ.update(DATABASE_URL="x", ADMIN_PASSWORD="admin-pw", OPENROUTER_API_KEY="")
src = (ROOT / "streamlit_app.py").read_text(encoding="utf-8")
code = compile(src, "streamlit_app.py", "exec")


def render(click=()):
    KEYS.clear(); TEXT.clear(); CLICK.clear(); CLICK.update(click)
    try:
        exec(code, {"__name__": "streamlit_app", "__file__": str(ROOT / "streamlit_app.py")})
    except Rerun:
        KEYS.clear(); TEXT.clear(); CLICK.clear()
        exec(code, {"__name__": "streamlit_app", "__file__": str(ROOT / "streamlit_app.py")})


def result():
    i = KEYS.index("result")
    return st.session_state.get("result_msg")


def login(no, pw):
    st.session_state.clear()
    st.session_state.update(login_student_no=no, login_password=pw)
    render({"login_submit"})


admin = pgshim.Conn(dbname="postgres")
admin.raw("drop database if exists tf_test")
admin.raw("create database tf_test")

fails = []


def expect(cond, msg):
    print(("OK  " if cond else "FAIL") + " " + msg)
    if not cond:
        fails.append(msg)


render()
expect({"login_student_no", "login_password", "login_submit", "result"} <= set(KEYS), "로그인 화면 key")
login("admin", "틀림")
expect(result().startswith("막힘"), f"틀린 관리자 비밀번호 → {result()}")
login("admin", "admin-pw")
expect({"whoami", "admin_roster_file", "admin_team_count", "admin_start", "admin_close", "unassigned",
        "admin_assign", "assign_status", "admin_confirm", "result"} <= set(KEYS), "관리자 화면 key")
st.session_state["admin_roster_file"] = Upload((ROOT / "tests/data/어려움.csv").read_bytes())
st.session_state["admin_team_count"] = 4
render({"admin_start"})
expect(result().startswith("승인"), f"명단 올리기 → {result()}")
expect({"team_1", "team_4"} <= set(KEYS), "관리자 화면 팀 표시")
admin_state = dict(st.session_state)


def as_student(no):
    st.session_state.clear()
    st.session_state.update(login_student_no=no, login_password="1234")
    render({"login_submit"})


as_student("2026301")
expect("whoami" in KEYS and any("학생1" in t for t in TEXT), "학생 로그인과 whoami")
expect({"apply_1", "apply_4", "team_1"} <= set(KEYS), "학생 화면 key")
render({"apply_1"})
expect(result().startswith("승인"), f"학생1 팀1 지원 → {result()}")
render()  # 실제 Streamlit 은 콜백을 먼저 실행하고 화면을 그리므로, 다시 그려 확인
expect("[단추 apply_2 비활성]" in TEXT, "팀이 생기면 지원 단추 비활성")
as_student("2026304")
render({"apply_1"})
expect(result().startswith("대기"), f"학생4 팀1 지원 → {result()}")
render()
expect("[단추 apply_3 비활성]" in TEXT, "대기 중이면 지원 단추 비활성")
as_student("2026301")
expect("vote_yes_2026304" in KEYS and "vote_no_2026304" in KEYS, "투표 단추 key")
render({"vote_yes_2026304"})
expect(result().startswith("승인"), f"학생1 찬성 → {result()}")
as_student("2026307")
render({"apply_1"})
as_student("2026301")
render({"vote_yes_2026307"})
render()
expect(result().startswith("대기") and any("내 투표: **찬성**" in t for t in TEXT) and "vote_yes_2026307" not in KEYS,
       "이미 투표한 지원은 단추를 숨기고 '내 투표: 찬성'")

st.session_state.clear(); st.session_state.update(admin_state)
render({"admin_close"})
expect(result().startswith("승인"), f"마감 → {result()}")
render({"admin_assign"})
expect(result().startswith("승인"), f"AI 배정(키 없음 → 3차까지 실패 → 앱 직접 배정) → {result()}")
expect(any("앱 직접 배정" in t for t in TEXT), "배정안 표시")
render({"admin_confirm"})
expect(result().startswith("승인"), f"확정 → {result()}")
as_student("2026316")
expect("[단추 apply_1 비활성]" in TEXT and any("내 팀" in t for t in TEXT), "확정 뒤 학생 화면에 내 팀 표시")
login("2026316", "1234")
st.session_state.clear(); st.session_state.update(login_student_no="2026316", login_password="1234")
render({"login_submit"})
st.session_state["user"]["is_admin"] = False
render({"admin_close"})
expect("admin_close" not in KEYS, "학생 계정에는 관리자 단추가 없음")
print("\n" + ("화면 점검 모두 통과" if not fails else f"실패 {len(fails)}건"))
sys.exit(1 if fails else 0)

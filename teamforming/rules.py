"""팀 편성 규칙 (공고 2.3, 2.5, 3장).

DB나 화면과 무관한 순수 함수만 둔다. 그래서 테스트하기 쉽고,
지원 단계·승인 직전 검사·AI 배정안 검사·앱의 직접 배정이 모두 같은 규칙을 쓴다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations

ROLES = ("기획자", "웹 프로그래머", "프론트엔드 디자이너", "테스터", "보고서 작성자", "발표자")
PLANNER = "기획자"
WEB = "웹 프로그래머"
REQUIRED_ROLES = (PLANNER, WEB)
MIN_SIZE = 2
MAX_SIZE = 4


@dataclass(frozen=True)
class Student:
    id: int
    student_no: str
    name: str
    main_role: str
    sub_roles: tuple[str, ...] = field(default_factory=tuple)

    def can(self, role: str) -> bool:
        """주 역할이나 보조 역할로 role을 맡을 수 있는지."""
        return self.main_role == role or role in self.sub_roles

    def roles_text(self) -> str:
        subs = ", ".join(self.sub_roles) if self.sub_roles else "없음"
        return f"주 역할 {self.main_role} / 보조 {subs}"


# ---------------------------------------------------------------- 필수 포함 역할
def required_filled(members: list[Student]) -> int:
    """기획자와 웹 프로그래머를 서로 다른 학생이 맡는다고 할 때 채울 수 있는 수(0~2).

    한 학생이 둘 다 할 수 있어도 하나로만 센다(공고 3장 '필수 포함 역할 자리 계산').
    """
    planners = [m for m in members if m.can(PLANNER)]
    webs = [m for m in members if m.can(WEB)]
    if any(p.id != w.id for p in planners for w in webs):
        return 2
    return 1 if (planners or webs) else 0


def missing_required_text(members: list[Student]) -> str:
    """팀에 모자란 필수 포함 역할을 사람이 읽을 문장으로."""
    has_pl = any(m.can(PLANNER) for m in members)
    has_wp = any(m.can(WEB) for m in members)
    if not has_pl and not has_wp:
        return "기획자와 웹 프로그래머를 맡을 학생이 없음"
    if not has_pl:
        return "기획자를 맡을 학생이 없음"
    if not has_wp:
        return "웹 프로그래머를 맡을 학생이 없음"
    return "기획자와 웹 프로그래머를 서로 다른 학생이 맡을 수 없음"


# ---------------------------------------------------------------- 지원·승인 검사
def join_block_reason(members: list[Student], newcomer: Student) -> str | None:
    """newcomer가 이 팀에 들어갈 수 없으면 이유, 들어갈 수 있으면 None.

    지원 단추를 누를 때(막힘)와 승인 직전 재검사(거절)에 똑같이 쓴다.
    """
    if len(members) >= MAX_SIZE:
        return "팀이 꽉 참"
    if any(m.main_role == newcomer.main_role for m in members):
        return "같은 주 역할의 팀원이 있음"
    after = members + [newcomer]
    remaining = MAX_SIZE - len(after)
    unfilled = len(REQUIRED_ROLES) - required_filled(after)
    if remaining < unfilled:
        return "필수 포함 역할(기획자, 웹 프로그래머)을 맡을 학생의 자리가 모자람"
    return None


# ---------------------------------------------------------------- 팀 구성 검사
def team_problems(team_name: str, members: list[Student]) -> list[str]:
    """확정 가능한 팀인지(인원, 주 역할 중복, 필수 포함 역할) 검사."""
    problems = []
    if not MIN_SIZE <= len(members) <= MAX_SIZE:
        problems.append(f"{team_name}의 인원이 {len(members)}명임(2~4명이어야 함)")
    seen: dict[str, str] = {}
    for m in members:
        if m.main_role in seen:
            problems.append(f"{team_name}에 주 역할 {m.main_role}이(가) 겹침({seen[m.main_role]}, {m.name})")
        seen[m.main_role] = m.name
    if members and required_filled(members) < 2:
        problems.append(f"{team_name}: {missing_required_text(members)}")
    return problems


def validate_assignment(
    teams: dict[int, list[Student]],
    team_names: dict[int, str],
    unassigned: list[Student],
    pairs: list[tuple[str, int]],
) -> tuple[list[str], dict[int, list[Student]]]:
    """배정안 검사(공고 2.5절 4항의 다섯 가지).

    pairs: (학번, 팀 번호) 목록. 반환: (문제 목록, 배정 뒤 팀 구성)
    """
    errors: list[str] = []
    by_no = {s.student_no: s for s in unassigned}
    counts: dict[str, int] = {}
    result = {tid: list(ms) for tid, ms in teams.items()}

    for no, tid in pairs:
        no = str(no).strip()
        if no not in by_no:
            errors.append(f"팀이 없는 학생이 아닌 학번이 배정안에 있음: {no}")
            continue
        if tid not in result:
            errors.append(f"없는 팀에 배정함: {by_no[no].name} → {tid}")
            continue
        counts[no] = counts.get(no, 0) + 1
        if counts[no] == 1:
            result[tid].append(by_no[no])

    # 1. 빠짐없이 배정
    missing = [s.name for s in unassigned if s.student_no not in counts]
    if missing:
        errors.append("배정되지 않은 학생이 있음: " + ", ".join(missing))
    # 2. 한 학생은 한 팀에만
    dup = [by_no[no].name for no, c in counts.items() if c > 1]
    if dup:
        errors.append("두 번 이상 배정된 학생이 있음: " + ", ".join(dup))
    # 3~5. 인원, 주 역할 중복, 필수 포함 역할
    for tid in sorted(result):
        errors.extend(team_problems(team_names.get(tid, f"팀{tid}"), result[tid]))
    return errors, result


# ---------------------------------------------------------------- 앱의 직접 배정
def solve_assignment(
    teams: dict[int, list[Student]], unassigned: list[Student]
) -> dict[int, list[Student]] | None:
    """모든 경우를 따져 규칙을 모두 지키는 배정을 끝까지 찾는다(백트래킹).

    찾으면 {팀 번호: 새로 넣을 학생 목록}, 없으면 None.
    """
    team_ids = sorted(teams)
    current = {tid: list(teams[tid]) for tid in team_ids}
    added: dict[int, list[Student]] = {tid: [] for tid in team_ids}

    # 넣을 곳이 적은 학생부터 정하면 빨리 막다른 길을 알아챈다.
    def options(s: Student) -> int:
        return sum(1 for t in team_ids if join_ok_basic(current[t], s))

    order = sorted(unassigned, key=lambda s: (options(s), s.student_no))

    def deficit_ok(index: int) -> bool:
        """남은 학생 수로 2명 미만 팀과 필수 역할을 아직 채울 수 있는지(가지치기)."""
        left = len(order) - index
        need = 0
        for t in team_ids:
            ms = current[t]
            free = MAX_SIZE - len(ms)
            need_people = max(MIN_SIZE - len(ms), len(REQUIRED_ROLES) - required_filled(ms))
            if need_people > free:
                return False
            need += need_people
        return need <= left

    def rec(i: int) -> bool:
        if not deficit_ok(i):
            return False
        if i == len(order):
            return all(not team_problems("", current[t]) for t in team_ids)
        s = order[i]
        # 사람이 적은 팀, 필수 역할이 모자란 팀부터 시도
        cands = sorted(
            team_ids,
            key=lambda t: (required_filled(current[t]), len(current[t]), t),
        )
        for t in cands:
            if not join_ok_basic(current[t], s):
                continue
            current[t].append(s)
            added[t].append(s)
            if rec(i + 1):
                return True
            current[t].pop()
            added[t].pop()
        return False

    return added if rec(0) else None


def join_ok_basic(members: list[Student], s: Student) -> bool:
    return len(members) < MAX_SIZE and all(m.main_role != s.main_role for m in members)


def infeasible_reasons(
    teams: dict[int, list[Student]], team_names: dict[int, str], unassigned: list[Student]
) -> list[str]:
    """규칙을 지키는 배정이 없을 때, 사람이 알아들을 이유를 찾는다."""
    reasons = []
    total = sum(len(m) for m in teams.values()) + len(unassigned)
    n = len(teams)
    if total < MIN_SIZE * n:
        reasons.append(f"학생 {total}명으로는 팀 {n}개를 모두 2명 이상으로 채울 수 없음")
    for tid in sorted(teams):
        ms = teams[tid]
        name = team_names.get(tid, f"팀{tid}")
        free = MAX_SIZE - len(ms)
        # 이 팀 하나만 놓고 봐도 고칠 수 없는지: 남은 학생 가운데 넣을 수 있는 조합을 모두 따짐
        cands = [s for s in unassigned if all(m.main_role != s.main_role for m in ms)]
        fixable = False
        for k in range(0, free + 1):
            for combo in combinations(cands, k):
                mains = [c.main_role for c in combo]
                if len(set(mains)) != len(mains):
                    continue
                after = ms + list(combo)
                if len(after) >= MIN_SIZE and required_filled(after) == 2:
                    fixable = True
                    break
            if fixable:
                break
        if not fixable:
            if free == 0:
                reasons.append(f"{name}은(는) 4명이 다 찼는데 {missing_required_text(ms)}")
            else:
                pool = ms + cands
                reasons.append(f"{name}에 {missing_required_text(pool) if required_filled(pool) < 2 else '규칙에 맞게 넣을 수 있는 학생 조합이 없음'}")
    if not reasons:
        reasons.append("팀마다 따로는 가능하지만, 남은 학생으로 모든 팀의 인원과 필수 포함 역할을 동시에 맞출 수 없음")
    return reasons

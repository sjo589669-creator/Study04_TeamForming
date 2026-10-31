"""마감 뒤 남은 학생의 배정(공고 2.5절, 3장, 4.3절).

순서: 규칙을 지키는 배정이 있는지 먼저 확인 → 1차·2차·3차 모델 → 그래도 실패하면 앱이 직접 배정.
모델마다 '부른 때부터 응답을 받을 때까지' 전체 120초를 재서 끊는다.
요청은 작업 스레드에서 하고, 화면 쪽은 0.5초마다 경과 시간을 갱신하며 기다린다.
"""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Callable

import requests

from . import rules
from .rules import Student

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL_TIMEOUT_SEC = 120
DEFAULT_MODELS = (
    "google/gemma-4-26b-a4b-it:free",
    "cohere/north-mini-code:free",
    "nvidia/nemotron-3.5-lightning:free",
    # openrouter/free(무료 모델 자동 연결 라우터)는 공고 6.3절 14번 감점 대상이라 쓰지 않음
)


@dataclass
class AssignOutcome:
    ok: bool                      # 배정안이 만들어졌는지
    message: str                  # result 안내("승인: …" / "막힘: …")
    status_text: str              # assign_status 에 남길 기록
    proposal: dict | None = None  # {"source": ..., "items": [{"student_no","team_id","name",...}]}


# ---------------------------------------------------------------- 프롬프트
def build_prompt(teams: dict[int, list[Student]], team_names: dict[int, str], unassigned: list[Student]) -> str:
    def who(s: Student) -> dict:
        return {"student_no": s.student_no, "name": s.name, "main_role": s.main_role, "sub_roles": list(s.sub_roles)}

    data = {
        "teams": [{"team": tid, "name": team_names[tid], "members": [who(s) for s in teams[tid]]}
                  for tid in sorted(teams)],
        "unassigned_students": [who(s) for s in unassigned],
    }
    return (
        "너는 대학 팀 프로젝트의 팀 편성 도우미다. 아래 JSON의 unassigned_students(팀이 없는 학생)를 "
        "기존 팀에 배정하라. 이미 팀에 있는 학생은 옮기지 않는다.\n\n"
        "규칙(모두 지켜야 함):\n"
        "1. 팀이 없는 학생은 빠짐없이, 한 학생은 한 팀에만 배정한다.\n"
        "2. 배정 뒤 모든 팀은 2~4명이어야 한다(빈 팀 없음).\n"
        "3. 한 팀 안에 main_role(주 역할)이 같은 학생이 2명 이상이면 안 된다.\n"
        "4. 모든 팀에는 '기획자'를 맡는 학생과 '웹 프로그래머'를 맡는 학생이 서로 다른 사람으로 1명씩 있어야 한다. "
        "main_role이나 sub_roles 가운데 하나로 그 역할을 맡을 수 있다. 한 학생이 두 역할을 동시에 맡을 수는 없다.\n"
        "5. 주 역할 중복은 main_role만 보고 따진다. sub_roles는 규칙 4를 따질 때만 쓴다.\n\n"
        "답은 설명 없이 아래 형식의 JSON 하나만 출력하라.\n"
        '{"assignments": [{"student_no": "학번", "team": 팀번호}, ...]}\n\n'
        "데이터:\n" + json.dumps(data, ensure_ascii=False, indent=1)
    )


# ---------------------------------------------------------------- 응답 해석
def parse_pairs(text: str) -> list[tuple[str, int]]:
    """모델 응답에서 (학번, 팀 번호) 목록을 꺼낸다. 형식이 틀리면 ValueError."""
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.S)
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("JSON 객체가 없음")
    obj = json.loads(t[start:end + 1])
    items = obj.get("assignments") if isinstance(obj, dict) else None
    if not isinstance(items, list):
        raise ValueError("assignments 목록이 없음")
    pairs = []
    for it in items:
        if not isinstance(it, dict) or "student_no" not in it or "team" not in it:
            raise ValueError(f"항목 형식이 틀림: {it}")
        team = it["team"]
        if isinstance(team, str):
            m = re.search(r"\d+", team)
            if not m:
                raise ValueError(f"팀 번호를 읽을 수 없음: {team}")
            team = int(m.group())
        pairs.append((str(it["student_no"]).strip(), int(team)))
    return pairs


# ---------------------------------------------------------------- 모델 호출
def _request(model: str, prompt: str, api_key: str, box: dict) -> None:
    """작업 스레드에서 실행. 결과는 box 에 담는다."""
    try:
        resp = requests.post(
            OPENROUTER_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
                     "X-Title": "Team Forming App"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 8000,
                "temperature": 0.2,
                "reasoning": {"effort": "low"},  # 추론 모델의 빈 응답을 줄임(4.3절)
            },
            timeout=(10, MODEL_TIMEOUT_SEC + 5),
        )
        box["status"] = resp.status_code
        try:
            body = resp.json()
        except ValueError:
            box["error"] = f"HTTP {resp.status_code}, JSON이 아닌 응답"
            return
        if resp.status_code != 200 or "error" in body:
            err = body.get("error", {}) if isinstance(body, dict) else {}
            msg = err.get("message") if isinstance(err, dict) else str(err)
            box["error"] = f"HTTP {resp.status_code}: {msg or '오류 응답'}"
            return
        choices = body.get("choices") or []
        content = (choices[0].get("message") or {}).get("content") if choices else None
        box["content"] = content or ""
    except requests.RequestException as e:
        box["error"] = f"요청 실패: {type(e).__name__}"
    except Exception as e:  # noqa: BLE001 - 어떤 실패든 다음 모델로 넘어가야 함
        box["error"] = f"처리 실패: {type(e).__name__}"


def call_model(model: str, prompt: str, api_key: str, on_tick: Callable[[float], None],
               timeout: float = MODEL_TIMEOUT_SEC) -> tuple[str | None, str, float]:
    """(내용, 실패 이유, 걸린 초). 성공하면 실패 이유는 ''.

    시간 제한은 '응답이 시작될 때까지'가 아니라 부른 때부터 끝날 때까지의 전체 시간이다.
    시간을 넘기면 작업 스레드를 기다리지 않고 버린다(데몬 스레드).
    """
    box: dict = {}
    th = threading.Thread(target=_request, args=(model, prompt, api_key, box), daemon=True)
    t0 = time.monotonic()
    th.start()
    while th.is_alive():
        elapsed = time.monotonic() - t0
        if elapsed >= timeout:
            return None, f"{int(timeout)}초 안에 응답하지 않아 끊음", elapsed
        on_tick(elapsed)
        th.join(timeout=min(0.5, timeout - elapsed))
    elapsed = time.monotonic() - t0
    if "error" in box:
        return None, box["error"], elapsed
    if not box.get("content", "").strip():
        return None, "빈 응답", elapsed
    return box["content"], "", elapsed


# ---------------------------------------------------------------- 전체 배정 흐름
def run_assignment(
    teams: dict[int, list[Student]],
    team_names: dict[int, str],
    unassigned: list[Student],
    models: list[str],
    api_key: str | None,
    progress: Callable[[str], None],
    log: Callable[[str, int, str, str, float, str], None],
    caller: Callable[..., tuple[str | None, str, float]] = call_model,
) -> AssignOutcome:
    run_id = time.strftime("%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    lines: list[str] = []

    def proposal_from(added: dict[int, list[Student]], source: str) -> dict:
        items = [{"student_no": s.student_no, "name": s.name, "main_role": s.main_role,
                  "sub_roles": list(s.sub_roles), "team_id": tid, "team_name": team_names[tid]}
                 for tid in sorted(added) for s in added[tid]]
        return {"source": source, "run_id": run_id, "items": items}

    # 0. 규칙을 지키는 배정이 있기는 한지(없으면 AI를 부르지 않음, 2.5절 8항)
    solution = rules.solve_assignment(teams, unassigned)
    if solution is None:
        reasons = rules.infeasible_reasons(teams, team_names, unassigned)
        text = "규칙을 지키는 배정이 없음\n" + "\n".join(f"- {r}" for r in reasons)
        return AssignOutcome(False, "막힘: 규칙을 지키는 배정이 없음 - " + "; ".join(reasons), text)

    if not unassigned:
        text = "팀이 없는 학생이 없음. 지금 팀 구성이 규칙을 모두 지키므로 그대로 확정하면 됨"
        return AssignOutcome(True, "승인: 배정할 학생이 없음(지금 구성으로 확정 가능)", text,
                             proposal_from({}, "배정할 학생 없음"))

    prompt = build_prompt(teams, team_names, unassigned)
    order_names = ["1차", "2차", "3차"]
    for i, model in enumerate(models[:3]):
        label = f"{order_names[i]} 모델({model})"
        if not api_key:
            reason, elapsed, content = "OPENROUTER_API_KEY가 없음", 0.0, None
        else:
            def tick(sec: float, label=label) -> None:
                progress("\n".join(lines + [f"⏳ {label}의 응답을 기다리는 중… {int(sec)}초 / {MODEL_TIMEOUT_SEC}초"]))
            progress("\n".join(lines + [f"⏳ {label}의 응답을 기다리는 중… 0초 / {MODEL_TIMEOUT_SEC}초"]))
            content, reason, elapsed = caller(model, prompt, api_key, tick)
        if content is not None:
            try:
                pairs = parse_pairs(content)
                errors, after = rules.validate_assignment(teams, team_names, unassigned, pairs)
            except (ValueError, json.JSONDecodeError) as e:
                errors, after = [f"응답 형식이 틀림({e})"], None
            if not errors:
                added = {tid: [s for s in after[tid] if s not in teams[tid]] for tid in after}
                lines.append(f"✅ {label}: 배정안이 검사 다섯 가지를 통과함({elapsed:.0f}초)")
                log(run_id, i + 1, model, "통과", elapsed, "검사 통과")
                return AssignOutcome(True, f"승인: {order_names[i]} 모델의 배정안이 검사를 통과함",
                                     "\n".join(lines), proposal_from(added, f"{order_names[i]} 모델 {model}"))
            reason = "배정안이 검사를 통과하지 못함 - " + "; ".join(errors[:4])
            log(run_id, i + 1, model, "검사 탈락", elapsed, reason + "\n응답: " + content[:1200])
        else:
            log(run_id, i + 1, model, "실패", elapsed, reason)
        nxt = f" → {order_names[i + 1]} 모델로 넘어감" if i + 1 < min(3, len(models)) else ""
        lines.append(f"❌ {label}: {reason}({elapsed:.0f}초){nxt}")
        progress("\n".join(lines))

    # 3차 모델까지 실패 → 앱이 모든 경우를 따져 찾은 배정(3장 어려운 해결)
    lines.append("🧮 3차 모델까지 실패하여 앱이 모든 경우를 따져 규칙을 지키는 배정을 찾음")
    log(run_id, 4, "앱 직접 배정", "통과", 0.0, "모든 경우 탐색")
    return AssignOutcome(True, "승인: 3차 모델까지 실패하여 앱이 직접 배정함(검사 통과)",
                         "\n".join(lines), proposal_from(solution, "앱 직접 배정(모든 경우 탐색)"))

"""Decision-quality evaluation harness for the allocation agent.

Runs the real ``allocation_decider_node`` (real LLM + real embeddings) against a
small golden set of scripted scenarios and asserts *behavioural invariants*, so
you can compare prompts/models and catch regressions.

Checks per case:
  * the action is one of the expected actions,
  * ``allocate``/``train`` reference one of the provided candidates and
    ``hire`` references none,
  * the guard invariants hold: a full skill match is never passed over for a
    hire, and a <50%-coverage role is not forced onto an internal candidate,
  * (optional) the justification cites a required skill.

Usage (from bench_forecast/):

    ./venv/bin/python scripts/eval_forecast.py

Exit code is non-zero if any case fails, so it can gate a model/prompt change.
"""

from __future__ import annotations

import sys
import time
import uuid
from datetime import date
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Initialise observability before importing the agent (the SDK must install
# its instrumentors before the first LangChain/Ollama client is created).
from src.core.observability import (  # noqa: E402
    flush_observability,
    init_observability,
    workflow,
)

init_observability()

from src.agents.nodes import _employee_skill_profile, _match_skills, allocation_decider_node  # noqa: E402


def _emp(name: str, skills: List[str], cost: float = 95.0, seniority: str = "Senior") -> Dict[str, Any]:
    eid = str(uuid.uuid4())
    return {
        "id": eid,
        "name": name,
        "full_name": name,
        "job_title": "Engineer",
        "seniority": seniority,
        "hourly_cost": cost,
        "hourly_cost_rate": cost,
        "skills": skills,
        "profile_text": f"{name}: {', '.join(skills)}",
        "bench_status": "bench",
        "available_from": str(date.today()),
    }


def _demand(role: str, skills: List[str], bill: float = 118.0, margin: float = 30.0) -> Dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "project_id": str(uuid.uuid4()),
        "role": role,
        "description": f"{role} requiring {', '.join(skills)}",
        "required_skills": skills,
        "target_bill_rate": bill,
        "target_margin": margin,
        "win_probability": 0.9,
    }


def _best_coverage(bench: List[Dict[str, Any]], required: List[str]):
    covs = []
    for e in bench:
        _m, _miss, cov = _match_skills(_employee_skill_profile(e), required)
        if cov is not None:
            covs.append(cov)
    return max(covs) if covs else None


CASES: List[Dict[str, Any]] = [
    {
        "name": "full_match -> allocate",
        "bench": [_emp("Ada", ["Java", "Spring", "SQL", "REST"])],
        "demand": _demand("Senior Java Backend", ["Java", "Spring", "SQL", "REST"]),
        "expect_actions": {"allocate"},
        "employee_required": True,
    },
    {
        "name": "partial match -> train/allocate",
        "bench": [_emp("Bob", ["Java", "SQL"])],
        "demand": _demand("Java Backend", ["Java", "Spring", "SQL", "REST"]),
        "expect_actions": {"train", "allocate"},
        "employee_required": True,
    },
    {
        "name": "no meaningful match -> hire",
        "bench": [_emp("Cara", ["Excel", "Communication"], cost=55.0, seniority="Junior")],
        "demand": _demand("Site Reliability Engineer", ["Kubernetes", "Go", "Prometheus"], bill=125.0),
        "expect_actions": {"hire"},
        "employee_required": False,
    },
    {
        "name": "grounded justification cites skills",
        "bench": [_emp("Dan", ["SQL", "Oracle", "MySQL"])],
        "demand": _demand("Database Developer", ["SQL", "Oracle", "MySQL"], bill=108.0, margin=28.0),
        "expect_actions": {"allocate", "train"},
        "employee_required": True,
        "justification_mentions_skill": True,
    },
]


def run_case(case: Dict[str, Any]) -> Dict[str, Any]:
    bench = case["bench"]
    demand = case["demand"]
    state = {"bench_employees": bench, "open_demands": [demand], "skill_matches": []}

    t0 = time.time()
    updates = allocation_decider_node(state)
    elapsed = time.time() - t0
    decision = (updates.get("decisions") or [{}])[0]

    action = decision.get("action_type")
    employee_id = decision.get("employee_id")
    justification = decision.get("justification") or ""
    bench_ids = {e["id"] for e in bench}
    best_cov = _best_coverage(bench, demand["required_skills"])

    failures: List[str] = []
    if action not in case["expect_actions"]:
        failures.append(f"action '{action}' not in {sorted(case['expect_actions'])}")

    if case["employee_required"]:
        if employee_id not in bench_ids:
            failures.append("allocate/train did not reference a provided candidate")
    elif employee_id is not None:
        failures.append("hire should have employee_id=null")

    # Guard invariants
    if action == "hire" and best_cov == 1.0:
        failures.append("hired despite a full skill match existing")
    if action in ("allocate", "train") and best_cov is not None and best_cov < 0.5:
        failures.append("allocated internally despite <50% coverage")

    if case.get("justification_mentions_skill") and action in ("allocate", "train"):
        if not any(str(s).lower() in justification.lower() for s in demand["required_skills"]):
            failures.append("justification does not cite any required skill")

    return {
        "name": case["name"],
        "action": action,
        "employee_id": employee_id,
        "best_cov": best_cov,
        "elapsed": round(elapsed, 1),
        "passed": not failures,
        "failures": failures,
        "justification": justification[:100],
    }


@workflow(
    name="bench-forecast-eval",
    workflow_id=f"eval-{int(time.time())}",
    version="v2",
    context={"suite": "allocation_decider", "cases": len(CASES)},
)
def main() -> int:
    print("=" * 78)
    print("Allocation agent evaluation")
    print("=" * 78)
    results = []
    for case in CASES:
        print(f"\n▶ {case['name']} …")
        try:
            r = run_case(case)
        except Exception as exc:  # pragma: no cover - env issues
            r = {"name": case["name"], "passed": False, "failures": [f"exception: {exc}"],
                 "action": "-", "employee_id": None, "best_cov": None, "elapsed": 0.0,
                 "justification": ""}
        results.append(r)
        status = "PASS" if r["passed"] else "FAIL"
        print(f"  {status}  action={r['action']}  best_cov={r['best_cov']}  {r['elapsed']}s")
        if r["justification"]:
            print(f"        just: {r['justification']}")
        for f in r["failures"]:
            print(f"        ✗ {f}")

    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    total_time = round(sum(r["elapsed"] for r in results), 1)
    print("\n" + "=" * 78)
    print(f"RESULT: {passed}/{total} cases passed   (total {total_time}s)")
    print("=" * 78)
    return 0 if passed == total else 1


if __name__ == "__main__":
    exit_code = main()
    flush_observability()
    raise SystemExit(exit_code)

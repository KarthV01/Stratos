from copy import deepcopy
from typing import Any

from app.challenges import quiet_worker, retention


CHALLENGES: list[dict[str, Any]] = [
    {
        "id": "vanishing-receipt",
        "title": "The Vanishing Receipt",
        "eyebrow": "Retention / Idempotency",
        "difficulty": "Intermediate",
        "playable": True,
        "summary": "A completed job returns after the system has forgotten completing it.",
        "brief": (
            "A worker applies a non-idempotent ledger change, but its acknowledgement "
            "is delayed. The short-lived completion receipt expires before the job is "
            "retried. At the same time, old task and event records accumulate forever."
        ),
        "objective": (
            "Keep enough history to prevent a valid retry from repeating the effect, "
            "while bounding data that is no longer needed for retry or audit."
        ),
        "requirements": [
            "Each unique task changes the ledger exactly once.",
            "Accepted work reaches a terminal state within the retry window.",
            "Recent results remain available for the audit window.",
            "Old task, event, receipt, and pending state stays bounded.",
        ],
        "scope": [
            "A task can be processed before its acknowledgement is recorded.",
            "Retry eligibility and memory retention are two different clocks.",
            "The broker, task history, event log, and completion receipts all retain state.",
        ],
    },
    {
        "id": "quiet-worker",
        "title": "The Quiet Worker",
        "eyebrow": "Failure Detection / Ownership",
        "difficulty": "Advanced",
        "playable": True,
        "summary": "A silent worker may be dead—or may still be changing the world.",
        "brief": (
            "One worker stops communicating during a long task but keeps executing. "
            "The controller reassigns its work. Later, a different worker genuinely "
            "dies, so waiting forever is not safe either."
        ),
        "objective": (
            "Recover work from dead workers without allowing an old, merely silent "
            "owner to publish a second or stale result."
        ),
        "requirements": [
            "Work owned by a dead worker is recovered before the liveness deadline.",
            "An obsolete owner cannot commit after ownership changes.",
            "Only one authoritative side effect is accepted per task.",
            "No task remains indefinitely pending.",
        ],
        "scope": [
            "Silence is an observation, not proof of death.",
            "Ownership can change while earlier computation continues.",
            "Detection, reassignment, and acceptance of a result are separate decisions.",
            "Define the timing assumptions your design depends on.",
        ],
    },
]

ROADMAP = [
    ("retry-avalanche", "Retry Avalanche", "Intermediate", "Timeouts amplify load through correlated retries, missing budgets, and absent jitter."),
    ("poison-letter", "Poison Letter", "Intermediate", "One permanently bad message monopolizes workers and starves healthy work."),
    ("infinite-waiting-room", "The Queue That Ate the Machine", "Intermediate", "Producers outrun consumers until latency and retained memory become the failure."),
    ("time-traveler", "Time Traveler", "Intermediate", "Late results and clock-skewed timestamps overwrite newer state."),
    ("two-commits", "Two Commits, One Truth", "Advanced", "A database write and broker publish disagree across a crash boundary."),
    ("noisy-neighbor", "Noisy Neighbor", "Intermediate", "One tenant or hot partition consumes shared capacity and blocks everyone else."),
    ("rolling-restart", "Rolling Restart Trap", "Intermediate", "Shutdown and deployment churn abandon in-flight work or process it twice."),
    ("split-brain", "Split-Brain Foreman", "Advanced", "Two coordinators both believe they own the same decisions during a partition."),
    ("clockwork-liar", "Clockwork Liar", "Advanced", "Clock jumps corrupt leases, deadlines, expiry, and apparent event order."),
    ("herd-at-dawn", "Herd at Dawn", "Intermediate", "Synchronized cache expiry sends every requester to the same recovering dependency."),
]

for challenge_id, title, difficulty, summary in ROADMAP:
    CHALLENGES.append(
        {
            "id": challenge_id,
            "title": title,
            "eyebrow": "Upcoming experiment",
            "difficulty": difficulty,
            "playable": False,
            "summary": summary,
            "brief": summary,
            "objective": "A future deterministic experiment will make this failure observable.",
            "requirements": [],
            "scope": [],
        }
    )


def get_challenge(challenge_id: str) -> dict[str, Any] | None:
    return next((item for item in CHALLENGES if item["id"] == challenge_id), None)


def strategy_for(challenge_id: str, implementation: str) -> dict[str, Any]:
    strategies = {
        "vanishing-receipt": retention,
        "quiet-worker": quiet_worker,
    }
    module = strategies[challenge_id]
    return deepcopy(module.BASELINE if implementation == "baseline" else module.CANDIDATE)


def retention_invariants(metrics: dict[str, int]) -> list[dict[str, Any]]:
    return [
        invariant("exactly-once-effect", "One effect per unique task", metrics.get("effect_count") == 1, f"observed {metrics.get('effect_count', 0)} ledger writes for 1 task"),
        invariant("terminal-progress", "Accepted work reaches a terminal state", metrics.get("completed") == 1, f"{metrics.get('completed', 0)} task reached completion"),
        invariant("recent-audit", "Recent result remains inspectable", metrics.get("recent_result_visible") == 1, "result record is available" if metrics.get("recent_result_visible") == 1 else "result record is missing"),
        invariant("bounded-retention", "Expired records are bounded", metrics.get("retained_records", 0) <= 500, f"{metrics.get('retained_records', 0)} old records remain; budget is 500"),
        invariant("pending-drained", "Expired pending work is resolved", metrics.get("pending_count", 0) == 0, f"{metrics.get('pending_count', 0)} broker entries remain pending"),
    ]


def quiet_worker_invariants(metrics: dict[str, int]) -> list[dict[str, Any]]:
    return [
        invariant("dead-worker-recovery", "Dead-worker task is recovered", metrics.get("dead_task_completed") == 1, f"completed={metrics.get('dead_task_completed', 0)}"),
        invariant("single-authority", "One authoritative effect per task", metrics.get("quiet_effect_count") == 1, f"observed {metrics.get('quiet_effect_count', 0)} effects for the quiet-worker task"),
        invariant("stale-owner-rejected", "Obsolete owner cannot replace the result", metrics.get("result_generation") == 2, f"accepted ownership generation {metrics.get('result_generation', 0)}; expected 2"),
        invariant("pending-drained", "Recovered sources leave no pending work", metrics.get("pending_count", 0) == 0, f"{metrics.get('pending_count', 0)} broker entries remain pending"),
    ]


def invariant(invariant_id: str, label: str, passed: bool, evidence: str) -> dict[str, Any]:
    return {"id": invariant_id, "label": label, "passed": passed, "evidence": evidence}

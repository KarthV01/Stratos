import unittest

from app.lab_models import (
    CHALLENGES,
    quiet_worker_invariants,
    retention_invariants,
    strategy_for,
)


class ChallengeCatalogTests(unittest.TestCase):
    def test_catalog_has_two_playable_and_ten_roadmap_challenges(self) -> None:
        self.assertEqual(2, sum(item["playable"] for item in CHALLENGES))
        self.assertEqual(10, sum(not item["playable"] for item in CHALLENGES))
        self.assertEqual(len(CHALLENGES), len({item["id"] for item in CHALLENGES}))

    def test_every_playable_challenge_has_a_decision_contract(self) -> None:
        for challenge in (item for item in CHALLENGES if item["playable"]):
            self.assertTrue(challenge["brief"])
            self.assertTrue(challenge["objective"])
            self.assertGreaterEqual(len(challenge["requirements"]), 4)
            self.assertGreaterEqual(len(challenge["scope"]), 3)

    def test_baseline_and_candidate_are_returned_as_independent_copies(self) -> None:
        candidate = strategy_for("vanishing-receipt", "candidate")
        candidate["receipt_ttl_seconds"] = 999
        self.assertEqual(2, strategy_for("vanishing-receipt", "candidate")["receipt_ttl_seconds"])
        self.assertEqual(2, strategy_for("vanishing-receipt", "baseline")["receipt_ttl_seconds"])


class InvariantTests(unittest.TestCase):
    def test_retention_baseline_evidence_fails_multiple_independent_requirements(self) -> None:
        results = retention_invariants({
            "effect_count": 2,
            "completed": 1,
            "recent_result_visible": 1,
            "retained_records": 2_500,
            "pending_count": 1,
        })
        self.assertEqual([False, True, True, False, False], [item["passed"] for item in results])

    def test_retention_complete_candidate_can_pass(self) -> None:
        results = retention_invariants({
            "effect_count": 1,
            "completed": 1,
            "recent_result_visible": 1,
            "retained_records": 500,
            "pending_count": 0,
        })
        self.assertTrue(all(item["passed"] for item in results))

    def test_quiet_worker_baseline_evidence_exposes_safety_and_cleanup(self) -> None:
        results = quiet_worker_invariants({
            "dead_task_completed": 1,
            "quiet_effect_count": 2,
            "result_generation": 1,
            "pending_count": 1,
        })
        self.assertEqual([True, False, False, False], [item["passed"] for item in results])

    def test_quiet_worker_complete_candidate_can_pass(self) -> None:
        results = quiet_worker_invariants({
            "dead_task_completed": 1,
            "quiet_effect_count": 1,
            "result_generation": 2,
            "pending_count": 0,
        })
        self.assertTrue(all(item["passed"] for item in results))


if __name__ == "__main__":
    unittest.main()

import unittest

from app.lab_models import CHALLENGES


class FieldGuideCatalogTests(unittest.TestCase):
    def test_all_twelve_studies_are_complete_demonstrations(self) -> None:
        self.assertEqual(12, len(CHALLENGES))
        self.assertEqual(len(CHALLENGES), len({item["id"] for item in CHALLENGES}))
        for study in CHALLENGES:
            self.assertTrue(study["problem"])
            self.assertTrue(study["guarantee"])
            self.assertGreaterEqual(len(study["safeguards"]), 4)
            self.assertGreaterEqual(len(study["steps"]), 5)

    def test_every_flow_shows_a_failure_and_a_failsafe(self) -> None:
        for study in CHALLENGES:
            tones = {step["tone"] for step in study["steps"]}
            self.assertIn("failure", tones, study["title"])
            self.assertIn("protected", tones, study["title"])

    def test_flow_steps_are_well_formed(self) -> None:
        allowed_tones = {"normal", "warning", "failure", "protected"}
        for study in CHALLENGES:
            for step in study["steps"]:
                self.assertTrue(step["from"])
                self.assertTrue(step["to"])
                self.assertTrue(step["message"])
                self.assertIn(step["tone"], allowed_tones)


if __name__ == "__main__":
    unittest.main()

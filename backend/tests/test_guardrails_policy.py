import pathlib
import sys
import unittest

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.guardrail_response_policy import render_guardrail_reply
from app.guardrails import evaluate_guardrails


class GuardrailsPolicyCases(unittest.TestCase):
    def test_decision_contract_for_toxic_hard_stop(self):
        decision = evaluate_guardrails("иди на хуй")
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.decision_code, "toxic_hard_stop")
        self.assertTrue(decision.stop_dialogue)
        self.assertGreaterEqual(decision.severity, 2)
        self.assertTrue(isinstance(decision.policy_tags, tuple))

    def test_decision_contract_for_clean_text(self):
        decision = evaluate_guardrails("Продажа пшеницы 200 тонн, Краснодар")
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.decision_code, "ok")
        self.assertEqual(decision.severity, 0)

    def test_response_policy_avoids_recent_exact_reply(self):
        decision = evaluate_guardrails("иди на хуй")
        first = render_guardrail_reply(decision, user_text="иди на хуй", last_assistant_messages=[])
        second = render_guardrail_reply(
            decision,
            user_text="иди на хуй",
            last_assistant_messages=[first],
        )
        self.assertNotEqual(first, second)

    def test_response_policy_has_variability_across_calls(self):
        decision = evaluate_guardrails("иди на хуй")
        replies = [render_guardrail_reply(decision, user_text="иди на хуй", last_assistant_messages=[]) for _ in range(6)]
        self.assertGreaterEqual(len(set(replies)), 2)


if __name__ == "__main__":
    unittest.main()

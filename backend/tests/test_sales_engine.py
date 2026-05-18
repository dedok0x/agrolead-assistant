import unittest
import pathlib
import sys

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.domain.sales import NextAction, SalesStage
from app.services.sales_engine import SalesEngine


class SalesEngineCases(unittest.TestCase):
    def setUp(self):
        self.engine = SalesEngine()

    def test_sale_keyword_sets_request_type_and_asks_product(self):
        decision = self.engine.handle("Продажа")
        self.assertEqual(decision.known_facts["request_type"], "sell_grain")
        self.assertEqual(decision.next_action, NextAction.ASK_PRODUCT)

    def test_product_after_sale_asks_volume(self):
        decision = self.engine.handle("пшеница", {"request_type": "sell_grain"})
        self.assertEqual(decision.known_facts["product"], "пшеница")
        self.assertEqual(decision.next_action, NextAction.ASK_VOLUME)

    def test_volume_after_product_asks_region(self):
        decision = self.engine.handle("400 тонн", {"request_type": "sell_grain", "product": "пшеница"})
        self.assertEqual(decision.known_facts["volume"], "400 тонн")
        self.assertEqual(decision.next_action, NextAction.ASK_REGION)

    def test_region_after_volume_asks_timing_or_contact(self):
        decision = self.engine.handle(
            "Краснодарский край",
            {"request_type": "sell_grain", "product": "пшеница", "volume": "400 тонн"},
        )
        self.assertEqual(decision.known_facts["region"], "Краснодарский край")
        self.assertIn(decision.next_action, {NextAction.ASK_TIMING, NextAction.ASK_CONTACT})

    def test_contact_grows_score(self):
        before = self.engine.handle(
            "срочно",
            {"request_type": "sell_grain", "product": "пшеница", "volume": "400 тонн", "region": "Краснодарский край"},
        )
        after = self.engine.handle("+79001234567", before.known_facts)
        self.assertIn("contact", after.known_facts)
        self.assertGreater(after.qualification_score, before.qualification_score)

    def test_millet_is_product_not_region(self):
        decision = self.engine.handle("пшено", {"request_type": "sell_grain"})
        self.assertEqual(decision.known_facts.get("product"), "пшено")
        self.assertNotIn("region", decision.known_facts)

    def test_does_not_repeat_product_question_when_product_known(self):
        decision = self.engine.handle("пшеница", {"request_type": "sell_grain", "product": "пшеница"})
        self.assertNotEqual(decision.next_action, NextAction.ASK_PRODUCT)

    def test_ready_for_manager_when_required_fields_collected(self):
        decision = self.engine.handle(
            "контакт +79001234567",
            {
                "request_type": "sell_grain",
                "product": "пшеница",
                "volume": "400 тонн",
                "region": "Краснодарский край",
                "timing": "июнь",
            },
        )
        self.assertEqual(decision.qualification_score, 100)
        self.assertEqual(decision.stage, SalesStage.READY_FOR_MANAGER)
        self.assertEqual(decision.next_action, NextAction.HANDOFF_MANAGER)


if __name__ == "__main__":
    unittest.main()

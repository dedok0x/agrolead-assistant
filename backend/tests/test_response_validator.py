import unittest
import pathlib
import sys

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.response_validator import ResponseValidator


class ResponseValidatorCases(unittest.TestCase):
    def setUp(self):
        self.validator = ResponseValidator()

    def test_price_without_context_is_replaced(self):
        text = self.validator.validate(
            "Цена будет 12000 руб/т.",
            next_action="ask_volume",
            known_facts={"request_type": "sell_grain", "product": "пшеница"},
            retrieved_context=[],
        )
        self.assertNotIn("12000", text)
        self.assertIn("объем", text.lower())

    def test_availability_without_context_is_replaced(self):
        text = self.validator.validate(
            "Пшеница есть в наличии.",
            next_action="ask_region",
            known_facts={"request_type": "buy_grain", "product": "пшеница", "volume": "400 тонн"},
            retrieved_context=[],
        )
        self.assertNotIn("есть в наличии", text.lower())
        self.assertIn("регион", text.lower())

    def test_question_for_known_field_is_replaced(self):
        text = self.validator.validate(
            "Какую культуру фиксируем?",
            next_action="ask_volume",
            known_facts={"request_type": "sell_grain", "product": "пшеница"},
            retrieved_context=[],
        )
        self.assertIn("объем", text.lower())


if __name__ == "__main__":
    unittest.main()

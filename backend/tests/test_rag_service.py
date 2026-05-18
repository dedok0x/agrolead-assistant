import unittest
import pathlib
import sys

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlmodel import Session, SQLModel, create_engine

from app.models import KnowledgeArticle
from app.services.rag_service import RAGService


class RAGServiceCases(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
        SQLModel.metadata.create_all(self.engine)

    def test_empty_database_returns_list(self):
        with Session(self.engine) as session:
            result = RAGService(session).retrieve_context("пшеница", None, {}, limit=3)
            self.assertIsInstance(result, list)
            self.assertEqual(result, [])

    def test_retrieve_context_returns_matching_article(self):
        with Session(self.engine) as session:
            session.add(
                KnowledgeArticle(
                    code="faq_price_policy",
                    title="Как формируется цена",
                    article_group="faq",
                    content_markdown="Цена зависит от культуры, объема, региона и качества.",
                    short_answer="Цена рассчитывается после уточнения параметров заявки.",
                    is_active=True,
                )
            )
            session.commit()
            result = RAGService(session).retrieve_context("цена пшеница", "buy_grain", {}, limit=3)
            self.assertTrue(result)
            self.assertEqual(result[0].source_type, "faq")


if __name__ == "__main__":
    unittest.main()

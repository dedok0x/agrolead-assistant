from sqlmodel import Session, select

from ..models import ProductItem


class ProductTool:
    def __init__(self, session: Session) -> None:
        self.session = session

    def _guess_culture(self, text: str, fallback: str = "") -> str:
        normalized = (text or "").lower()
        if "пшен" in normalized:
            return "Пшеница"
        if "ячмен" in normalized:
            return "Ячмень"
        if "кукуруз" in normalized:
            return "Кукуруза"
        return fallback

    def _guess_grade(self, text: str, fallback: str = "") -> str:
        normalized = (text or "").lower()
        for grade in ("1 класс", "2 класс", "3 класс", "4 класс", "5 класс", "6 класс"):
            if grade in normalized:
                return grade
        if "фураж" in normalized:
            return "Фуражная"
        if "продов" in normalized:
            return "Продовольственная"
        return fallback

    def lookup(self, query: str, fallback_culture: str = "", fallback_grade: str = "", limit: int = 3) -> list[ProductItem]:
        culture = self._guess_culture(query, fallback=fallback_culture)
        grade = self._guess_grade(query, fallback=fallback_grade)

        items = list(self.session.exec(select(ProductItem).where(ProductItem.active == True)).all())
        items.sort(key=lambda item: item.stock_tons, reverse=True)
        ordered = items

        if culture:
            ordered = [item for item in ordered if item.culture.lower() == culture.lower()]

        if grade:
            grade_lower = grade.lower()
            exact = [item for item in ordered if grade_lower in (item.grade or "").lower() or grade_lower in (item.name or "").lower()]
            if exact:
                ordered = exact

        return ordered[: max(1, limit)]

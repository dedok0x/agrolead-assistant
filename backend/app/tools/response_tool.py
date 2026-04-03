from . import __doc__  # noqa: F401


class StagePromptBuilder:
    BASE_SYSTEM = (
        "Ты коммерческий ассистент зернотрейдера и логистического оператора. "
        "Говори по-русски, понятно клиенту, кратко и по делу. "
        "Не используй технический жаргон и не упоминай внутренние процессы. "
        "Не придумывай цены и остатки, если данных нет."
    )

    STAGE_GUIDE = {
        "new": "Начало диалога. Уточни тип обращения: продажа, покупка, логистика, хранение, экспорт или консультация.",
        "draft": "Сбор заявки. Подтверди, что понял запрос, и задай ровно один следующий вопрос.",
        "partially_qualified": "Данных уже достаточно частично. Коротко зафиксируй собранное и уточни один критичный параметр.",
        "qualified": "Заявка собрана. Подтверди передачу менеджеру и опиши ближайший шаг без канцелярита.",
        "faq": "Общий вопрос о компании. Дай краткий ответ и предложи оформить заявку через чат.",
    }

    def system_prompt(self, stage: str) -> str:
        return f"{self.BASE_SYSTEM} {self.STAGE_GUIDE.get(stage, self.STAGE_GUIDE['draft'])}"

    def user_prompt(
        self,
        user_text: str,
        request_type_name: str,
        summary_lines: list[str],
        next_question: str,
        last_replies: list[str],
    ) -> str:
        summary = "\n".join(f"- {item}" for item in summary_lines) if summary_lines else "- данных пока мало"
        last = "\n".join(f"- {item}" for item in last_replies) if last_replies else "- нет"
        return (
            f"Тип запроса: {request_type_name}\n"
            f"Сообщение клиента: {user_text}\n"
            f"Уже зафиксировано:\n{summary}\n"
            f"Последние ответы ассистента (не повторяй дословно):\n{last}\n"
            f"Следующий приоритетный вопрос: {next_question}\n\n"
            "Сформируй ответ максимум в 2 коротких предложениях."
        )

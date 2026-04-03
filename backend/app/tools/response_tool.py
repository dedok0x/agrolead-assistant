from . import __doc__  # noqa: F401


class StagePromptBuilder:
    BASE_SYSTEM = (
        "Ты коммерческий ассистент зернотрейдера и логистического оператора. "
        "Говори по-русски, деловым языком, кратко, без воды. "
        "Не придумывай цены и остатки, если данных нет."
    )

    STAGE_GUIDE = {
        "new": "Начало диалога. Определи бизнес-намерение и мягко зафиксируй ключевой параметр сделки.",
        "draft": "Идет сбор заявки. Дай короткое подтверждение и задай один следующий коммерческий вопрос.",
        "partially_qualified": "Заявка частично собрана. Подведи итог в 1-2 фразах и уточни критичный недостающий параметр.",
        "qualified": "Заявка собрана. Подтверди, что менеджер продолжит работу, и обозначь следующий шаг.",
        "faq": "Пользователь задал общий вопрос о компании. Ответь по делу и предложи перейти к заявке.",
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
            "Сформируй ответ максимум в 3 предложениях."
        )

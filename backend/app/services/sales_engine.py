from __future__ import annotations

from typing import Any

from ..domain.sales import NextAction, SalesDecision, SalesStage, UserSignal
from .field_extractor import ExtractedFact, FieldExtractor
from .lead_qualification_service import LeadQualificationService


class SalesEngine:
    def __init__(self, qualifier: LeadQualificationService | None = None, extractor: FieldExtractor | None = None) -> None:
        self.qualifier = qualifier or LeadQualificationService()
        self.extractor = extractor or FieldExtractor()

    def handle(
        self,
        text: str,
        known_facts: dict[str, Any] | None = None,
        *,
        current_next_action: str | None = None,
        user_signal: str | None = None,
    ) -> SalesDecision:
        known = self.qualifier.normalize_known_facts(known_facts or {})
        extracted_items = self.extractor.extract(
            text,
            current_next_action=current_next_action,
            known_facts=known,
            user_signal=user_signal,
        )
        rejected_fields = [fact.field for fact in extracted_items if fact.status == "rejected"]
        for field in rejected_fields:
            known.pop(field, None)
        extracted = {
            fact.field: fact.normalized_value
            for fact in extracted_items
            if fact.status == "confirmed" and fact.confidence >= self.extractor.confirmed_threshold
        }
        uncertain = {
            fact.field: {
                "value": fact.value,
                "normalized_value": fact.normalized_value,
                "confidence": fact.confidence,
                "source_text": fact.source_text,
            }
            for fact in extracted_items
            if fact.status == "uncertain" or fact.confidence < self.extractor.confirmed_threshold
        }
        merged = {**known, **extracted}
        merged = self.qualifier.normalize_known_facts(merged)

        signal = user_signal or UserSignal.PROVIDES_FACT.value
        is_faq = signal in {UserSignal.ASKS_CAPABILITIES.value, UserSignal.ASKS_CLARIFICATION.value}
        is_objection = signal == UserSignal.OBJECTION_PRICE.value
        if signal == UserSignal.IRRELEVANT.value:
            return SalesDecision(
                known_facts=merged,
                captured_fields=self.qualifier.captured_fields(merged),
                missing_fields=self.qualifier.missing_fields(merged),
                qualification_score=self.qualifier.qualification_score(merged),
                stage=SalesStage.IRRELEVANT,
                next_action=NextAction.REFUSE_IRRELEVANT,
                request_type=merged.get("request_type"),
                intent="irrelevant",
                extracted_fields=list(extracted),
                uncertain_facts=uncertain,
                rejected_fields=rejected_fields,
                user_signal=signal,
            )

        missing = self.qualifier.missing_fields(merged)
        score = self.qualifier.qualification_score(merged)
        next_action = self._next_action(missing, is_faq=is_faq, is_objection=is_objection)
        stage = self._stage_for(score, next_action, bool(merged.get("request_type")))

        return SalesDecision(
            known_facts=merged,
            captured_fields=self.qualifier.captured_fields(merged),
            missing_fields=missing,
            qualification_score=score,
            stage=stage,
            next_action=next_action,
            request_type=merged.get("request_type"),
            intent=merged.get("request_type") or next_action.value,
            extracted_fields=list(extracted),
            uncertain_facts=uncertain,
            rejected_fields=rejected_fields,
            user_signal=signal,
            is_faq=is_faq,
            is_objection=is_objection,
        )

    def extract(self, text: str, known_facts: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            fact.field: fact.normalized_value
            for fact in self.extractor.extract(text, current_next_action=None, known_facts=known_facts or {})
            if fact.status == "confirmed" and fact.confidence >= self.extractor.confirmed_threshold
        }

    def _next_action(self, missing: list[str], *, is_faq: bool, is_objection: bool) -> NextAction:
        if is_objection:
            return NextAction.HANDLE_OBJECTION
        if is_faq and missing:
            return {
                "request_type": NextAction.ASK_REQUEST_TYPE,
                "product": NextAction.ASK_PRODUCT,
                "volume": NextAction.ASK_VOLUME,
                "region": NextAction.ASK_REGION,
                "timing": NextAction.ASK_TIMING,
                "contact": NextAction.ASK_CONTACT,
            }.get(missing[0], NextAction.ANSWER_FAQ)
        if not missing:
            return NextAction.HANDOFF_MANAGER
        field = missing[0]
        return {
            "request_type": NextAction.ASK_REQUEST_TYPE,
            "product": NextAction.ASK_PRODUCT,
            "volume": NextAction.ASK_VOLUME,
            "region": NextAction.ASK_REGION,
            "timing": NextAction.ASK_TIMING,
            "contact": NextAction.ASK_CONTACT,
        }.get(field, NextAction.ASK_REQUEST_TYPE)

    @staticmethod
    def _stage_for(score: int, next_action: NextAction, has_request_type: bool) -> SalesStage:
        if next_action == NextAction.HANDOFF_MANAGER or score >= 100:
            return SalesStage.READY_FOR_MANAGER
        if not has_request_type:
            return SalesStage.NEW
        if score >= 75:
            return SalesStage.NEEDS_DISCOVERY
        return SalesStage.QUALIFICATION

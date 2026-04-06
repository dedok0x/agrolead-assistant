# Architecture vNext

## Current Direction
The project remains a modular monolith with extraction-ready boundaries.

## Target Modules
- `api` layer: route handlers and transport-only DTO mapping.
- `application` layer: orchestration use cases.
- `domain` layer: qualification, negotiation, offer policy, guardrails contract.
- `infra` layer: repositories, LLM adapters, retrieval services.

## New Building Blocks
- `guardrails.py`: decision-only policy contract.
- `guardrail_response_policy.py`: response text policy and anti-repeat.
- `rag_service.py`: knowledge retrieval and context rendering.
- `security.py`: password/session security primitives.

## Service Extraction Readiness
Boundaries are prepared for future service split:
- auth/session service,
- dialogue/orchestration service,
- catalog/reference service,
- knowledge/retrieval service,
- CRM workflow service.

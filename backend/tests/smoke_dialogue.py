"""Smoke-прогон реальных диалоговых сценариев против работающего API.

Запуск (API должен быть поднят, например через docker compose):

    API_BASE_URL=http://127.0.0.1:8000 python backend/tests/smoke_dialogue.py

Скрипт проверяет 7 сценариев: покупка, продажа, логистика, хранение, FAQ,
неполная заявка и продолжение сессии. Выход с кодом 1 при любой ошибке.
"""

from __future__ import annotations

import os
import sys
import uuid

import httpx

BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
FAILURES: list[str] = []


def check(name: str, condition: bool, details: str = "") -> None:
    mark = "OK " if condition else "FAIL"
    print(f"[{mark}] {name}" + (f" — {details}" if details and not condition else ""))
    if not condition:
        FAILURES.append(f"{name}: {details}")


def send(client: httpx.Client, text: str, client_id: str, session_id=None) -> dict:
    response = client.post(
        f"{BASE_URL}/api/chat",
        json={"text": text, "client_id": client_id, "session_id": session_id},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def run() -> int:
    suffix = uuid.uuid4().hex[:6]
    with httpx.Client() as client:
        health = client.get(f"{BASE_URL}/api/health", timeout=10).json()
        check("health: status ok", health.get("status") == "ok", str(health))
        check("health: db_ok", health.get("db_ok") is True)

        # 1. Покупатель
        p = send(client, "Здравствуйте, хочу купить 100 тонн пшеницы с доставкой в Краснодар. Телефон +7 900 111-22-33.", f"smoke-buy-{suffix}")
        check("buy: request_type", p["known_facts"].get("request_type") == "buy_grain", str(p["known_facts"]))
        check("buy: volume", p["known_facts"].get("volume") == "100 тонн")
        check("buy: region", p["known_facts"].get("region") == "Краснодарский край")
        check("buy: contact", "contact" in p["known_facts"])
        check("buy: lead created", bool(p["lead_id"]))
        check("buy: score", p["qualification_score"] >= 80, str(p["qualification_score"]))

        # 2. Поставщик
        p = send(client, "Есть 400 тонн пшеницы 3 класса в Краснодарском крае, готовы продать, звоните +7 918 222-33-44.", f"smoke-sell-{suffix}")
        check("sell: request_type", p["known_facts"].get("request_type") == "sell_grain", str(p["known_facts"]))
        check("sell: quality", p["known_facts"].get("quality_class") == "3 класс")
        check("sell: lead created", bool(p["lead_id"]))

        # 3. Логистика
        p = send(client, "Нужно перевезти 250 тонн ячменя из Ростовской области в Новороссийск на следующей неделе.", f"smoke-log-{suffix}")
        check("logistics: request_type", p["known_facts"].get("request_type") == "logistics", str(p["known_facts"]))
        check("logistics: route", "->" in str(p["known_facts"].get("region", "")))
        check("logistics: next ask contact", p["next_action"] == "ask_contact", p["next_action"])

        # 4. Хранение
        p = send(client, "Интересует хранение 300 тонн подсолнечника в Краснодаре, срок два месяца.", f"smoke-store-{suffix}")
        check("storage: request_type", p["known_facts"].get("request_type") == "storage", str(p["known_facts"]))
        check("storage: timing", "месяц" in str(p["known_facts"].get("timing", "")))

        # 5. FAQ
        p = send(client, "Какие документы нужны для поставки зерна?", f"smoke-faq-{suffix}")
        check("faq: no lead", p["lead_id"] is None, str(p["lead_id"]))
        check("faq: status faq_only", p["status"] == "faq_only", p["status"])
        check("faq: answer_faq", p["next_action"] == "answer_faq", p["next_action"])

        # 6. Неполная заявка
        p = send(client, "Хочу купить пшеницу.", f"smoke-incomplete-{suffix}")
        check("incomplete: draft", p["status"] == "draft", p["status"])
        check("incomplete: ask volume", p["next_action"] == "ask_volume", p["next_action"])

        # 7. Продолжение сессии: дозаполняем заявку из п.6
        session_id = p["session_id"]
        asked = [p["next_action"]]
        for text in ["200 тонн", "в Краснодар", "в июне", "+7 900 111-22-33"]:
            p = send(client, text, f"smoke-incomplete-{suffix}", session_id)
            asked.append(p["next_action"])
        check("continue: same session", p["session_id"] == session_id)
        check("continue: qualified", p["status"] == "qualified", p["status"])
        check("continue: score 100", p["qualification_score"] == 100, str(p["qualification_score"]))
        check("continue: missing empty", p["missing_fields"] == [], str(p["missing_fields"]))
        check("continue: no repeated questions", len([a for a in asked if a.startswith("ask_")]) == len(set(a for a in asked if a.startswith("ask_"))), str(asked))
        check("continue: lead exists", bool(p["lead_id"]))

    print()
    if FAILURES:
        print(f"SMOKE FAILED: {len(FAILURES)} problem(s)")
        for failure in FAILURES:
            print(f"  - {failure}")
        return 1
    print("SMOKE PASSED: all dialogue scenarios ok")
    return 0


if __name__ == "__main__":
    sys.exit(run())

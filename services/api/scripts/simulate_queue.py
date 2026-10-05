"""Create synthetic visits through the ordinary kiosk and patient API path."""

import argparse
import asyncio
import os
import uuid
from typing import Any

import httpx


def _raise(response: httpx.Response) -> dict[str, Any]:
    response.raise_for_status()
    return response.json()


async def main(total: int, urgent: int) -> None:
    origin = os.getenv("API_ORIGIN", "http://localhost:8000").rstrip("/")
    kiosk_token = os.getenv("SIMULATE_KIOSK_TOKEN", "")
    activation_code = os.getenv("SIMULATE_KIOSK_CODE", "")
    if not kiosk_token and len(activation_code) < 40:
        raise SystemExit("Set SIMULATE_KIOSK_CODE to a synthetic kiosk's one-time activation code.")
    async with httpx.AsyncClient(base_url=origin, timeout=30) as client:
        if not kiosk_token:
            kiosk = _raise(
                await client.post("/kiosks/activate", json={"activation_code": activation_code})
            )
            kiosk_token = kiosk["access_token"]
        kiosk_headers = {"Authorization": f"Bearer {kiosk_token}"}

        async def make_visit(index: int) -> dict[str, str]:
            session = _raise(
                await client.post(
                    "/intake/sessions", headers=kiosk_headers, json={"language": "en"}
                )
            )
            visit_id = session["visit_id"]
            patient_headers = {"Authorization": f"Bearer {session['patient_access_token']}"}
            consent = session["consent"]
            _raise(
                await client.post(
                    f"/intake/sessions/{visit_id}/consent",
                    headers=patient_headers,
                    json={
                        "version": consent["version"],
                        "sha256": consent["sha256"],
                        "granted": True,
                    },
                )
            )
            _raise(
                await client.post(
                    f"/intake/sessions/{visit_id}/identity",
                    headers=patient_headers,
                    json={"age": 8 if index % 2 else 35, "sex": "unknown"},
                )
            )
            # This tile endpoint is available only after authenticating as the synthetic patient.
            danger = _raise(
                await client.get(
                    f"/intake/sessions/{visit_id}/danger-tiles", headers=patient_headers
                )
            )
            tile_ids = [danger["tiles"][0]["id"]] if index < urgent else []
            _raise(
                await client.post(
                    f"/intake/sessions/{visit_id}/danger-signs",
                    headers=patient_headers,
                    json={"tile_ids": tile_ids},
                )
            )
            while True:
                next_turn = _raise(
                    await client.get(
                        f"/intake/sessions/{visit_id}/interview/next-question",
                        headers=patient_headers,
                    )
                )
                if next_turn.get("complete"):
                    break
                question = next_turn["question"]
                optional = question.get("optional", False)
                answer: str | int | None
                answer = (
                    None
                    if optional
                    else (
                        0
                        if question["input"] == "scale"
                        else f"Synthetic demo response {index + 1}"
                    )
                )
                _raise(
                    await client.post(
                        f"/intake/sessions/{visit_id}/interview/answers",
                        headers={**patient_headers, "Idempotency-Key": str(uuid.uuid4())},
                        json={
                            "question_id": question["id"],
                            "answer": answer,
                            "skip": optional,
                            "input_mode": "touch",
                        },
                    )
                )
            result = _raise(
                await client.post(
                    f"/intake/sessions/{visit_id}/confirm",
                    headers={**patient_headers, "Idempotency-Key": str(uuid.uuid4())},
                )
            )
            return {"token": result["token"], "url": f"{origin}/s/{result['status_token']}"}

        results = await asyncio.gather(*(make_visit(index) for index in range(total)))
        print(f"Created {len(results)} synthetic visits ({urgent} with a danger tile).")
        for result in results:
            print(f"{result['token']}  {result['url']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=int(os.getenv("N", "50")))
    parser.add_argument("--urgent", type=int, default=int(os.getenv("URGENT", "10")))
    arguments = parser.parse_args()
    if arguments.count < 1 or not 0 <= arguments.urgent <= arguments.count:
        raise SystemExit("Require count >= 1 and 0 <= urgent <= count.")
    asyncio.run(main(arguments.count, arguments.urgent))

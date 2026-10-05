"""Send synthetic sign-in requests to demonstrate the API's 10/minute limit."""

import asyncio
from uuid import uuid4

import httpx


async def main() -> None:
    base_url = "http://localhost:8000"
    payload = {
        "hospital_id": str(uuid4()),
        "email": "rate-limit-demo@example.test",
        "password": "synthetic-invalid-password",
    }
    async with httpx.AsyncClient(base_url=base_url, timeout=5) as client:
        for attempt in range(1, 12):
            response = await client.post("/auth/login", json=payload)
            print(
                f"Attempt {attempt}: HTTP {response.status_code} "
                f"Retry-After={response.headers.get('Retry-After', '-')}"
            )


if __name__ == "__main__":
    asyncio.run(main())

"""Phase 0 worker process placeholder."""

import asyncio


async def main() -> None:
    """Stay alive until background task queues are introduced in later phases."""
    while True:
        await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(main())

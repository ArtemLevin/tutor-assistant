from __future__ import annotations

import asyncio
from typing import Any

import httpx

from .protocol import CancellationToken


async def _request_async(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None,
    payload: dict[str, Any] | None,
    timeout_seconds: float,
    trust_env: bool,
    cancellation: CancellationToken | None,
) -> httpx.Response:
    async with httpx.AsyncClient(
        timeout=timeout_seconds,
        trust_env=trust_env,
        follow_redirects=True,
    ) as client:
        task = asyncio.create_task(client.request(method, url, headers=headers, json=payload))
        try:
            while True:
                if cancellation and cancellation.cancelled:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                    cancellation.raise_if_cancelled()
                done, _pending = await asyncio.wait({task}, timeout=0.1)
                if done:
                    response = await task
                    response.raise_for_status()
                    return response
        finally:
            if not task.done():
                task.cancel()


def cancellable_request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
    timeout_seconds: float,
    trust_env: bool,
    cancellation: CancellationToken | None = None,
) -> httpx.Response:
    if cancellation:
        cancellation.raise_if_cancelled()
    return asyncio.run(
        _request_async(
            method,
            url,
            headers=headers,
            payload=payload,
            timeout_seconds=timeout_seconds,
            trust_env=trust_env,
            cancellation=cancellation,
        )
    )

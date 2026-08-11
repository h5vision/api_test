from __future__ import annotations

from collections.abc import Collection


def wants_chat_sse(
    *,
    stream: bool | None,
    input_fields: Collection[str],
    accept_header: str | None,
) -> bool:
    """Resolve Chat transport without requiring a Body-only control field.

    An explicit boolean remains the backward-compatible override. When the
    field is omitted or null, standard HTTP Accept negotiation owns transport.
    """

    if "stream" in input_fields and stream is not None:
        return bool(stream)
    accepted = str(accept_header or "").lower()
    return "text/event-stream" in accepted

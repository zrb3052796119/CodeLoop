from __future__ import annotations

import pytest


TURN_ID = "turn_" + "a" * 32


def test_cancellation_token_is_process_local_idempotent_and_content_free() -> None:
    from minicode.turn_cancellation import (
        TurnCancellationRequested,
        TurnCancellationToken,
    )

    token = TurnCancellationToken(TURN_ID)

    assert token.turn_id == TURN_ID
    assert token.is_requested() is False
    token.raise_if_requested()
    assert token.request() is True
    assert token.request() is False
    assert token.is_requested() is True
    with pytest.raises(TurnCancellationRequested) as raised:
        token.raise_if_requested()
    assert raised.value.turn_id == TURN_ID
    assert not hasattr(token, "message")
    assert not hasattr(token, "reason")


def test_none_checkpoint_is_an_exact_noop() -> None:
    from minicode.turn_cancellation import raise_if_cancelled

    assert raise_if_cancelled(None) is None

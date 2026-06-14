"""Phase 7.2: bounded backoff + DLQ replay riding the process-once claim.

Hermetic — no DB. The replay's no-double-process property is proven with a claim-aware
fake handler that mimics ``flip_if_queued`` (process iff still 'queued'); the real path
uses ``IngestRepository.flip_if_queued``.
"""

from freight.interfaces.types import QueueMessage
from freight.mocks.dispatcher import LocalDispatcher


class _RecordingSleep:
    """Records backoff delays instead of waiting."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


# --- bounded backoff ----------------------------------------------------------------


async def test_backoff_is_capped_exponential_and_bounded() -> None:
    async def always_fail(_message: QueueMessage) -> None:
        raise RuntimeError("poison")

    sleeper = _RecordingSleep()
    dispatcher = LocalDispatcher(
        always_fail, retries=5, base_delay=1.0, max_delay=4.0, sleep=sleeper
    )
    await dispatcher.deliver(QueueMessage(id="p"))

    # 6 attempts total (retries+1); a delay BEFORE each of the 5 retries, none after the
    # final attempt. Exponential 1,2,4,8,16 capped at max_delay=4 → bounded.
    assert sleeper.delays == [1.0, 2.0, 4.0, 4.0, 4.0]
    assert dispatcher.attempts == 6
    assert dispatcher.dead_letter == [QueueMessage(id="p")]


async def test_no_backoff_on_first_attempt_success() -> None:
    async def ok(_message: QueueMessage) -> None:
        return None

    sleeper = _RecordingSleep()
    dispatcher = LocalDispatcher(ok, retries=3, sleep=sleeper)
    await dispatcher.deliver(QueueMessage(id="m"))
    assert sleeper.delays == []  # delivered first try → never slept


# --- DLQ replay ---------------------------------------------------------------------


class _ClaimAwareHandler:
    """Mimics the process-once claim: handle a message at most ONCE per id.

    ``fail_ids`` always raise (poison). Any other id processes exactly once; a repeat
    delivery of an already-processed id is a no-op (mirrors flip_if_queued → 0 rows).
    """

    def __init__(self, fail_ids: set[str] | None = None) -> None:
        self.processed: list[str] = []
        self.no_ops: list[str] = []
        self._fail = fail_ids or set()

    async def __call__(self, message: QueueMessage) -> None:
        if message.id in self._fail:
            raise RuntimeError("poison")
        if message.id in self.processed:
            self.no_ops.append(message.id)  # claim already taken → do nothing
            return
        self.processed.append(message.id)


async def test_replay_recovers_a_transiently_failed_message() -> None:
    # First delivery fails (DB was down); after recovery the handler succeeds on replay.
    handler = _ClaimAwareHandler(fail_ids={"m1"})
    dispatcher = LocalDispatcher(handler, retries=1, sleep=_RecordingSleep())
    await dispatcher.deliver(QueueMessage(id="m1"))
    assert dispatcher.dead_letter == [QueueMessage(id="m1")]

    handler._fail.clear()  # "DB" is back
    result = await dispatcher.replay()

    assert result.replayed == 1
    assert result.re_dead_lettered == 0
    assert dispatcher.dead_letter == []
    assert handler.processed == ["m1"]  # processed exactly once


async def test_replay_of_already_processed_message_does_not_double_process() -> None:
    # The key constraint: replay rides the same claim, so re-delivering an already-done
    # message no-ops instead of processing it twice.
    handler = _ClaimAwareHandler()
    dispatcher = LocalDispatcher(handler, retries=1, sleep=_RecordingSleep())

    await dispatcher.deliver(QueueMessage(id="done"))  # processed once
    assert handler.processed == ["done"]

    # Force it into the DLQ and replay it (simulating a lost ack / manual re-queue).
    dispatcher.dead_letter.append(QueueMessage(id="done"))
    result = await dispatcher.replay()

    assert result.replayed == 1  # "delivered" (handler returned), but...
    assert handler.processed == ["done"]  # ...still processed exactly once
    assert handler.no_ops == ["done"]  # the replay was a claim no-op


async def test_replay_re_dead_letters_persistent_poison() -> None:
    handler = _ClaimAwareHandler(fail_ids={"poison"})
    dispatcher = LocalDispatcher(handler, retries=1, sleep=_RecordingSleep())
    await dispatcher.deliver(QueueMessage(id="poison"))
    assert dispatcher.dead_letter == [QueueMessage(id="poison")]

    result = await dispatcher.replay()  # still poison → back to the DLQ (bounded)
    assert result.replayed == 0
    assert result.re_dead_lettered == 1
    assert dispatcher.dead_letter == [QueueMessage(id="poison")]

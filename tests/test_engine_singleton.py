"""The engine factory is a process-level singleton per URL (the connection-leak fix).

Hermetic: ``create_engine`` is lazy (no DB connection at construction), so engine
identity can be asserted offline. This locks the fix that closed the engine-per-request
connection leak — route deps now share ONE pool.
"""

from freight.db import get_engine, make_engine


def test_get_engine_returns_the_same_singleton_per_url() -> None:
    url = "postgresql://u:p@h:6543/singleton_same"
    assert get_engine(url) is get_engine(url)  # one shared Engine / one pool


def test_get_engine_distinguishes_urls() -> None:
    a = get_engine("postgresql://u:p@h:6543/singleton_a")
    b = get_engine("postgresql://u:p@h:6543/singleton_b")
    assert a is not b


def test_make_engine_stays_uncached_for_isolated_lifecycle() -> None:
    # Scripts/tests that manage their own dispose() must get a fresh engine each call.
    url = "postgresql://u:p@h:6543/uncached"
    assert make_engine(url) is not make_engine(url)

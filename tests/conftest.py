import pytest

import main


@pytest.fixture(autouse=True)
def fresh_rate_limits():
    """The limiters are module-level and would otherwise carry counts from one
    test into the next — a suite that hits an endpoint nine times would start
    failing on the ninth for reasons that have nothing to do with the test."""
    main.DECK_LIMITER._hits.clear()
    main.EXPLORE_LIMITER._hits.clear()
    yield
    main.DECK_LIMITER._hits.clear()
    main.EXPLORE_LIMITER._hits.clear()

"""The machinery behind the app, split out from the FastAPI wiring.

`main.py` keeps the app, the routes and the mutable singletons — the limiters,
the provider pool and the explore pools. Everything here is either pure or
reads only the environment, which is what makes it testable without a server.

The split follows the test files rather than a taxonomy: config, providers,
limits, deck and explore each already had their own suite.
"""

"""Route-level regression client; test_auth exercises the real auth middleware.

Existing fixtures directly seed the legacy database and test route behavior in
isolation. Keep that boundary explicit rather than weakening production auth.
"""
from fastapi import FastAPI
from starlette.testclient import TestClient as StarletteTestClient


def TestClient(app, **kwargs):
    core = FastAPI(exception_handlers=app.exception_handlers)
    core.router.routes = app.router.routes
    return StarletteTestClient(core, **kwargs)

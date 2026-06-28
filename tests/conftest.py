"""Shared pytest fixtures.

Critically, this sets ``LIBRESEERR_DATA_DIR`` to a throwaway temp directory
*before* ``app`` is imported anywhere, so importing the app (which loads config
and creates a default admin at import time) never touches the real ``data/``
directory.
"""
import os
import tempfile

# Must run before any `import app` in the test modules. pytest imports conftest
# before collecting test modules, so this env var is in place in time.
os.environ.setdefault(
    "LIBRESEERR_DATA_DIR", tempfile.mkdtemp(prefix="libreseerr-tests-")
)

import pytest  # noqa: E402

import app as app_module  # noqa: E402


@pytest.fixture
def flask_app():
    """The Flask application object, in testing mode."""
    app_module.app.config.update(TESTING=True)
    return app_module.app


@pytest.fixture
def client(flask_app):
    """An unauthenticated Flask test client."""
    return flask_app.test_client()

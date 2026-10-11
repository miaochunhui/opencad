"""Request-local validation of final output destinations.

Standalone cadcore callers retain their existing output behavior. A hosted
workspace supplies its own validator for every derived path before writing.
Context variables keep parallel requests isolated without global IO patches.
"""

from contextlib import contextmanager
from contextvars import ContextVar


_validator = ContextVar("cadcore_output_validator", default=None)


def guard_output(path):
    validator = _validator.get()
    return str(path) if validator is None else validator(path)


@contextmanager
def output_guard(validator):
    token = _validator.set(validator)
    try:
        yield
    finally:
        _validator.reset(token)

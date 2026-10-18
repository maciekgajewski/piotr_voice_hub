"""Process-wide correlation identifiers (docs/microphone-protocol.md, MP-ID-001/002).

One factory for every id type: a random per-process prefix plus one monotonic counter, so an id
is never reused during the process lifetime, across reconnects and driver instances. Like the
old project's conversations/id_factory.py. Never use per-connection counters.
"""

import itertools
import uuid

_PREFIX = uuid.uuid4().hex[:12]
_COUNTER = itertools.count(1)


def new_id(kind: str) -> str:
    """Return a new id such as `utterance-3f2a9c1b04de-17`."""
    assert kind and "-" not in kind, kind
    return f"{kind}-{_PREFIX}-{next(_COUNTER)}"

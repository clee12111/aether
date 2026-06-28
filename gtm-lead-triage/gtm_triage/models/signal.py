"""Signal protocol — the generic input surface for any Motion.

Any object with these five fields can enter run_motion(). Lead already
satisfies this protocol; future inbound channels (email parse, chat,
webhook) and outbound list rows will too.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Signal(Protocol):
    email: str
    name: str
    company: str
    message: str
    source: str

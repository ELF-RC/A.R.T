# pyright: reportUnnecessaryTypeIgnoreComment=false
# pyright: reportIgnoreCommentWithoutRule=false
# pyright: reportUnreachable=false
# pyright: reportExplicitAny=false
# pyright: reportAny=false
import os
import sys
from typing import (
    Any,
    Protocol,
    TypeVar,
    runtime_checkable,
)

if sys.version_info >= (3, 12):
    from typing import override

else:
    # typing.override is 3.12+; fall back to a no-op decorator so the
    # parser keeps working without the typing_extensions dependency.
    def override(func):
        return func


@runtime_checkable
class ReadableStream(Protocol):
    def read(self, size: int | None = -1, /) -> bytes: ...

    def tell(self) -> int: ...

    def seek(self, offset: int, whence: int = os.SEEK_SET, /) -> int: ...


@runtime_checkable
class PeekableStream(ReadableStream, Protocol):
    def peek(self, size: int = 0, /) -> bytes: ...


T = TypeVar("T")


def assert_cast(obj: Any, t: type[T], /) -> T:
    assert isinstance(obj, t), f"Object is: {type(obj)} not {t}"
    return obj


__all__ = ["PeekableStream", "ReadableStream", "assert_cast", "override"]

"""Identity: the one place names become keys.

`norm_key`, `album_key` and `title_key` are the only normalizers in the codebase. SQL never
re-implements them; every other module imports from here. The golden file at
tests/unit/golden/normalize.json is the contract — change the function and the
golden file together, on purpose.
"""

from musicdata.identity.normalize import (
    album_key,
    has_edition_marker,
    norm_key,
    primary_artist,
    split_featured,
    strip_edition_markers,
    title_key,
)

__all__ = [
    "album_key",
    "has_edition_marker",
    "norm_key",
    "primary_artist",
    "split_featured",
    "strip_edition_markers",
    "title_key",
]

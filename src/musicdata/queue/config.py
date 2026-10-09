"""Every tuning constant of the queue (docs/QUEUE_SPEC.md sections 7–9). One-line changes."""

from __future__ import annotations

# Scoring (section 7)
TIER = {"Essential": 3.0, "Recommended": 2.0, "Deep cut": 1.0}
DEFAULT_TIER = "Recommended"
RANKED_LAST_FACTOR = 0.5  # a ranked list's last entry counts half its first
START_HERE = 1.25
AFFINITY_LISTENS_CAP = 500  # artist listens at which affinity stops growing
LANE_GAP = 0.3  # up to +30% for an album in a little-heard atlas lane
BUMP = 2.0
BUMP_DAYS = 14

# Building (section 8)
DEFAULT_N = 10
MAX_N = 50
WILDCARD_RANKS = (51, 500)  # the wildcard comes from these ranks of the scored list
SHUFFLE_POOL = 200  # Shuffle draws the new slots from the top this-many candidates

# Revisit pools (section 9), in fill order
POOLS = ("spaced", "abandoned", "unfinished")
SPACED_DAYS = {1: 14, 2: 60, 3: 240}  # full sessions → days before the next listen is due
ABANDONED_FULLS = 3
ABANDONED_LISTENS = 40
ABANDONED_DAYS = 180
UNFINISHED_DAYS = 14
REVISIT_REST_DAYS = 30  # any revisit: last session at least this long ago (unfinished: 14)

# Short labels for why lines; other lists use their name
LIST_LABELS = {
    "v_atlas": "V Atlas",
    "rolling_stone_500": "Rolling Stone",
    "1001_albums": "1001 Albums",
    "aoty_2007_2024": "AOTY",
    "acclaimed_music_3000": "Acclaimed Music",
    "claude_canon": "Claude canon",
    "besteveralbums_overall": "BestEverAlbums",
}

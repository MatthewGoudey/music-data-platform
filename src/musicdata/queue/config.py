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
    "billboard_200": "Billboard 200",
    "rym_top_5000": "Rate Your Music",
}

# Graph connections (docs/graph/COMPANION_SPEC.md section 4)
CONNECTION_WEIGHT = {
    "person_core": 1.0,  # member, producer or songwriter on the candidate
    "person_session": 0.6,  # engineer, mixer or session player
    "person_mastering": 0.3,
    "lineage": 1.0,  # the candidate sounds like or was influenced by a heard artist or album
    "cover": 0.8,  # the candidate covers or borrows a heard artist's first recording
    "studio": 0.3,  # both recorded at one studio
}
DAMP_ALBUMS = 10  # damp(n) = 1 / (1 + ln(1 + albums(n) / DAMP_ALBUMS)) for people and studios
LISTEN_BASE = 0.5  # listen(n) = min(1, LISTEN_BASE + LISTEN_STEP × full sessions it reaches)
LISTEN_STEP = 0.1
CONNECTION_TOP = 3  # nodes kept per candidate for card lines
THREAD_DAYS = 14  # threads start from albums finished in the last this-many days
THREAD_FROM = 10  # the newest this-many of them
THREAD_TO = 20  # connected unheard albums kept per finished album
C_CAP = 3  # C(g) at which graph affinity stops growing (spec 5.2)
CARD_LINES = 2  # connection lines on a card (spec 5.1)

# Tastebreaker (queue spec v20): a genre Matt has not played lately, the Claude canon first
TASTEBREAKER_DAYS = 60  # "lately": albums with a session in the last this-many days
TASTEBREAKER_POOL = 25  # the day's pick is drawn from the best this-many breakers
TASTEBREAKER_FIRST = "claude_canon"  # tier 1: Matt's most varied list
MORE_N = 20  # albums per page when the list scrolls on (spec v20)

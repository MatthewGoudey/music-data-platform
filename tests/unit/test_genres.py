from collections import Counter

from musicdata.queue.genres import best_names, genre_key, index_rows, key_of, split_genres, title


def test_spellings_share_a_key():
    same = ["singer-songwriter", "singer/songwriter", "Singer Songwriter"]
    assert len({genre_key(s) for s in same}) == 1
    assert genre_key("rock & roll") == genre_key("rock and roll")
    assert genre_key("bossanova") == genre_key("bossa nova")
    assert genre_key("drumand bass") == genre_key("drum and bass")
    assert genre_key("reggaetón") == genre_key("reggaeton")


def test_split_drops_notes_groups_and_junk():
    assert split_genres("Jazz (specifically vocal jazz, bebop), Soul") == ["jazz", "soul"]
    assert split_genres("rock; classic rock") == ["rock", "classic rock"]
    assert split_genres("trap[edm], hip hop; none") == ["trap", "hip hop"]
    assert split_genres("Folk, World, & Country") == ["folk", "world"]
    assert split_genres("\xa0indie rock") == ["indie rock"]
    assert split_genres(None) == []


def test_lost_characters_come_back_through_aliases_or_go():
    assert split_genres("Musique concrï¿½te") == ["musique concrète"]
    assert split_genres("rock; musique concr?te") == ["rock", "musique concrète"]
    assert split_genres("Tropicï¿½lia") == ["tropicália"]
    assert split_genres("Unknownï¿½genre") == []  # no alias: dropped, never shown broken


def test_aliases_merge_discogs_styles():
    assert key_of("Glam") == genre_key("glam rock")
    assert key_of("Prog Rock") == genre_key("progressive rock")


def test_best_name_prefers_word_breaks_then_accents():
    names = best_names({"ukbass": Counter({"ukbass": 27, "uk bass": 2})})
    assert names["ukbass"] == "uk bass"
    names = best_names({"musiqueconcrete": Counter({"musique concrète": 1, "musique concrete": 3})})
    assert names["musiqueconcrete"] == "musique concrète"  # the alias target wins
    assert split_genres("Musiqueconcrète") == ["musique concrète"]


def test_index_rows_merges_sources():
    by_rg, names = index_rows([(1, "Synth-pop, New Wave"), (1, "Synthpop"), (2, "synthpop")])
    assert by_rg[1] == {"synthpop", "newwave"}
    assert names["synthpop"] == "synthpop"


def test_title_keeps_acronyms():
    assert title("uk bass") == "UK bass"
    assert title("musique concrète") == "Musique concrète"
    assert title("idm") == "IDM"

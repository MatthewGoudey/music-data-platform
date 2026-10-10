"""Entity upserts shared by `graph batch load-claims` and `POST /assertions`: one entity per
MusicBrainz ID (one per artist across artist and person), else one per type, name key and context.
"""

from __future__ import annotations

import json

import asyncpg

from musicdata.identity import norm_key

ART = {"artist", "person"}


async def upsert_entity(
    conn: asyncpg.Connection,
    type_: str,
    name: str,
    *,
    mbid: str | None = None,
    attrs: dict | None = None,
    status: str = "resolved",
    context: str = "",
    keep_status: bool = False,
) -> int:
    """The entity's id, created when missing; `attrs` merge into an existing row. With
    `keep_status`, a name-only upsert leaves an existing row's `resolve_status` as it is (a name
    posted as written never downgrades an entity resolution already settled)."""
    key = norm_key(name) or name.casefold()
    attrs_json = json.dumps(attrs or {}, default=str)
    if mbid and type_ in ART:
        sql = """INSERT INTO entity (type, name, norm_key, mbid, attrs)
                 VALUES ($1, $2, $3, $4, $5::jsonb)
                 ON CONFLICT (mbid) WHERE mbid IS NOT NULL AND type IN ('artist','person')
                    DO UPDATE SET attrs = entity.attrs || EXCLUDED.attrs
                 RETURNING entity_id"""
        args: tuple = (type_, name, key, mbid, attrs_json)
    elif mbid:
        sql = """INSERT INTO entity (type, name, norm_key, mbid, attrs, context_key)
                 VALUES ($1, $2, $3, $4, $5::jsonb, $6)
                 ON CONFLICT (type, mbid) WHERE mbid IS NOT NULL
                                          AND type NOT IN ('artist','person')
                    DO UPDATE SET attrs = entity.attrs || EXCLUDED.attrs
                 RETURNING entity_id"""
        args = (type_, name, key, mbid, attrs_json, context)
    else:
        status_update = "entity.resolve_status" if keep_status else "EXCLUDED.resolve_status"
        sql = f"""INSERT INTO entity (type, name, norm_key, attrs, resolve_status, context_key)
                  VALUES ($1, $2, $3, $4::jsonb, $5, $6)
                  ON CONFLICT (type, norm_key, context_key) WHERE mbid IS NULL AND local_key IS NULL
                     DO UPDATE SET attrs = entity.attrs || EXCLUDED.attrs,
                                   resolve_status = {status_update}
                  RETURNING entity_id"""
        args = (type_, name, key, attrs_json, status, context)
    return await conn.fetchval(sql, *args)

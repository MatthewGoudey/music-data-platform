"""Create the musicdata_ro role in one environment, or rotate its password, and record its URL.

    uv run python scripts/readonly_role.py dev

Reads DATABASE_URL from .env.<env>, writes DATABASE_URL_READONLY back to it, and prints
only the role name and a check result, never the password. Then set the same value on
the app: flyctl secrets set --config fly.<env>.toml DATABASE_URL_READONLY=...
POST /query uses this role when DATABASE_URL_READONLY is set.
"""

import asyncio
import pathlib
import secrets
import sys
from urllib.parse import quote, urlsplit, urlunsplit

import asyncpg

ROOT = pathlib.Path(__file__).resolve().parents[1]
ROLE = "musicdata_ro"


def read_env(path: pathlib.Path) -> dict[str, str]:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            out[k] = v
    return out


async def main(env: str) -> None:
    path = ROOT / f".env.{env}"
    values = read_env(path)
    owner_url = values["DATABASE_URL"]
    password = secrets.token_urlsafe(24)
    conn = await asyncpg.connect(owner_url)
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", ROLE)
        verb = "ALTER" if exists else "CREATE"
        await conn.execute(f"{verb} ROLE {ROLE} LOGIN PASSWORD '{password}'")
        await conn.execute(f"GRANT pg_read_all_data TO {ROLE}")
        await conn.execute(f"ALTER ROLE {ROLE} SET default_transaction_read_only = on")
        await conn.execute(f"ALTER ROLE {ROLE} SET statement_timeout = '10s'")
    finally:
        await conn.close()

    parts = urlsplit(owner_url)
    host = parts.netloc.split("@", 1)[1]
    ro_url = urlunsplit(parts._replace(netloc=f"{ROLE}:{quote(password, safe='')}@{host}"))

    ro = await asyncpg.connect(ro_url)
    try:
        n = await ro.fetchval("SELECT count(*) FROM listen")
        try:
            await ro.execute("DELETE FROM listen WHERE false")
            write_blocked = False
        except asyncpg.PostgresError:
            write_blocked = True
    finally:
        await ro.close()

    lines = [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.startswith("DATABASE_URL_READONLY=")
    ]
    lines.append(f"DATABASE_URL_READONLY={ro_url}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{env}: {verb.lower()}d {ROLE}; reads {n} listens; writes blocked: {write_blocked}")


asyncio.run(main(sys.argv[1]))

import os
import re
from datetime import datetime, timezone

import requests
import spotipy

# =========================
# ENV / CONFIG
# =========================
SOURCE_PLAYLIST_ID = os.environ["SOURCE_PLAYLIST_ID"].strip()

CLIENT_ID = os.environ["SPOTIFY_CLIENT_ID"].strip()
CLIENT_SECRET = os.environ["SPOTIFY_CLIENT_SECRET"].strip()
REFRESH_TOKEN = os.environ["SPOTIFY_REFRESH_TOKEN"].strip()

# Marker so we only delete OUR clones, never your original playlist
CLONE_TAG = "[AUTOCLONE]"
DESC_TAG_PREFIX = "autoclone_source="


# =========================
# AUTH
# =========================
def get_access_token() -> str:
    r = requests.post(
        "https://accounts.spotify.com/api/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": REFRESH_TOKEN,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def spotify_client() -> spotipy.Spotify:
    token = get_access_token()
    return spotipy.Spotify(auth=token)


# =========================
# HELPERS
# =========================
def normalize_playlist_id(value: str) -> str:
    value = value.strip()

    # Full URL: https://open.spotify.com/playlist/<ID>?si=...
    if "open.spotify.com" in value and "/playlist/" in value:
        value = value.split("/playlist/")[1]
        value = value.split("?")[0]
        value = value.split("/")[0]

    # URI: spotify:playlist:<ID>
    if value.startswith("spotify:playlist:"):
        value = value.split("spotify:playlist:")[1]

    return value


SOURCE_PLAYLIST_ID = normalize_playlist_id(SOURCE_PLAYLIST_ID)


def get_all_track_uris(sp: spotipy.Spotify, playlist_id: str) -> list[str]:
    uris: list[str] = []
    results = sp.playlist_items(playlist_id, additional_types=["track", "episode"])

    while results:
        for item in results.get("items", []):
            track = item.get("track")
            if not track:
                continue

            uri = track.get("uri")
            if not uri:
                continue

            # Only keep real Spotify track URIs
            # Skip local files and anything else
            if uri.startswith("spotify:track:"):
                uris.append(uri)

        results = sp.next(results) if results.get("next") else None

    return uris



def iter_user_playlists(sp: spotipy.Spotify):
    results = sp.current_user_playlists(limit=50)
    while results:
        for pl in results.get("items", []):
            yield pl
        results = sp.next(results) if results.get("next") else None


def parse_clone_timestamp(name: str):
    # Expected ending: "[AUTOCLONE] 2025-12-23 10:00 UTC"
    m = re.search(r"\[AUTOCLONE\]\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}\s+UTC)$", name)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d %H:%M UTC").replace(tzinfo=timezone.utc)
    except Exception:
        return None


def find_clones(sp: spotipy.Spotify, source_id: str):
    clones = []
    for pl in iter_user_playlists(sp):
        pid = pl.get("id")
        name = pl.get("name", "")
        desc = pl.get("description") or ""

        if not pid:
            continue

        # Only consider playlists we created (tag + matching source ID in description)
        if CLONE_TAG in name and f"{DESC_TAG_PREFIX}{source_id}" in desc:
            ts = parse_clone_timestamp(name)
            clones.append((ts, pid, name))

    return clones


def delete_old_clones_keep_newest(sp: spotipy.Spotify, source_id: str):
    clones = find_clones(sp, source_id)

    # Safety: never delete the original by accident
    clones = [c for c in clones if c[1] != source_id]

    # Prefer timestamped sorting
    with_ts = [c for c in clones if c[0] is not None]
    without_ts = [c for c in clones if c[0] is None]

    with_ts.sort(key=lambda x: x[0], reverse=True)

    to_delete = []
    if with_ts:
        # keep the newest timestamped clone
        to_delete.extend(with_ts[1:])
        # and delete any weird un-timestamped clones too
        to_delete.extend(without_ts)
    else:
        # no timestamps found, delete all clones we matched (tag + desc)
        to_delete.extend(without_ts)

    for _, pid, pname in to_delete:
        sp.current_user_unfollow_playlist(pid)
        print(f"Deleted old clone: {pname} ({pid})")


# =========================
# MAIN
# =========================
def main():
    sp = spotify_client()
    user_id = sp.current_user()["id"]

    # Read source playlist name (original is never modified)
    source_playlist = sp.playlist(SOURCE_PLAYLIST_ID)
    source_name = source_playlist["name"]

    # Delete older clones first (keep newest)
    delete_old_clones_keep_newest(sp, SOURCE_PLAYLIST_ID)

    # Create a fresh clone with a timestamp so we can reliably sort later
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    clone_name = f"{source_name} {CLONE_TAG} {stamp}"

    track_uris = get_all_track_uris(sp, SOURCE_PLAYLIST_ID)

    new_playlist = sp.user_playlist_create(
        user=user_id,
        name=clone_name,
        public=False,
        description=f"Auto-refreshed every 24 hours (GitHub Actions). {DESC_TAG_PREFIX}{SOURCE_PLAYLIST_ID}",
    )

    for i in range(0, len(track_uris), 100):
        sp.playlist_add_items(new_playlist["id"], track_uris[i:i + 100])

    print(f"Created new clone: {new_playlist['id']}")


if __name__ == "__main__":
    main()

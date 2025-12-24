import os
import re
from datetime import datetime, timezone

import requests
import spotipy

import base64
# =========================
# ENV / CONFIG
# =========================
SOURCE_PLAYLIST_ID = os.environ["SOURCE_PLAYLIST_ID"].strip()

CLIENT_ID = os.environ["SPOTIFY_CLIENT_ID"].strip()
CLIENT_SECRET = os.environ["SPOTIFY_CLIENT_SECRET"].strip()
REFRESH_TOKEN = os.environ["SPOTIFY_REFRESH_TOKEN"].strip()

# Hidden marker in description so we can find clones without changing the visible name
DESC_TAG_PREFIX = "autoclone_source="
ZERO_WIDTH_SPACE = "\u200B"  # often renders as visually blank


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

    if "open.spotify.com" in value and "/playlist/" in value:
        value = value.split("/playlist/")[1]
        value = value.split("?")[0]
        value = value.split("/")[0]

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


def extract_source_id_from_desc(desc: str) -> str | None:
    # Find autoclone_source=<id> anywhere in the description (even if prefixed by zero width space)
    m = re.search(r"autoclone_source=([A-Za-z0-9]{22})", desc or "")
    return m.group(1) if m else None


def find_clones(sp: spotipy.Spotify, source_id: str):
    clones = []
    for pl in iter_user_playlists(sp):
        pid = pl.get("id")
        desc = pl.get("description") or ""
        if not pid:
            continue

        tagged_source = extract_source_id_from_desc(desc)
        if tagged_source == source_id:
            clones.append(pid)

    return clones


def delete_old_clones_keep_newest(sp: spotipy.Spotify, source_id: str):
    clones = find_clones(sp, source_id)

    # Safety: never delete the original source playlist
    clones = [pid for pid in clones if pid != source_id]

    if not clones:
        return

    # We will keep the most recently created clone by using playlist "added_at" order in your library.
    # current_user_playlists returns in the order Spotify shows in your library (recent-ish),
    # but to be safe we will keep the last one we see in that order and delete the rest.
    # Simpler: keep the newest created in THIS run and delete all previously tagged ones before creation.
    for pid in clones:
        sp.current_user_unfollow_playlist(pid)
        print(f"Deleted old clone: {pid}")

def copy_playlist_cover(sp: spotipy.Spotify, source_id: str, target_id: str):
    images = sp.playlist_cover_image(source_id)
    if not images:
        return

    image_url = images[0]["url"]

    r = requests.get(image_url, timeout=30)
    r.raise_for_status()

    image_bytes = r.content

    # Spotify requires base64-encoded JPEG
    image_b64 = base64.b64encode(image_bytes).decode("utf-8")

    sp.playlist_upload_cover_image(target_id, image_b64)

# =========================
# MAIN
# =========================
def main():
    sp = spotify_client()
    user_id = sp.current_user()["id"]

    source_playlist = sp.playlist(SOURCE_PLAYLIST_ID)
    source_name = source_playlist["name"]

    # Delete all previously tagged clones BEFORE creating the new one
    delete_old_clones_keep_newest(sp, SOURCE_PLAYLIST_ID)

    track_uris = get_all_track_uris(sp, SOURCE_PLAYLIST_ID)

    hidden_desc = f"{ZERO_WIDTH_SPACE}{DESC_TAG_PREFIX}{SOURCE_PLAYLIST_ID}"

    new_playlist = sp.user_playlist_create(
        user=user_id,
        name=source_name,          # same name as original
        public=False,
        description=hidden_desc,   # looks blank in most clients
    )

    try:
        copy_playlist_cover(sp, SOURCE_PLAYLIST_ID, new_playlist["id"])
    except Exception as e:
        print(f"Cover copy failed (non-fatal): {e}")

    for i in range(0, len(track_uris), 100):
        sp.playlist_add_items(new_playlist["id"], track_uris[i:i + 100])

    print(f"Created new clone: {new_playlist['id']}")

    spotify_url = f"https://open.spotify.com/playlist/{new_playlist['id']}"
    with open("latest.html", "w", encoding="utf-8") as f:
        f.write(f"""<!doctype html>
<html>
  <head>
    <meta http-equiv="refresh" content="0; url={spotify_url}" />
  </head>
  <body>
    Redirecting to latest playlist…
  </body>
</html>
""")


if __name__ == "__main__":
    main()

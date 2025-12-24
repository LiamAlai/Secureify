import os
import base64
import requests
import spotipy

SOURCE_PLAYLIST_ID = os.environ["SOURCE_PLAYLIST_ID"].strip()

CLIENT_ID = os.environ["SPOTIFY_CLIENT_ID"].strip()
CLIENT_SECRET = os.environ["SPOTIFY_CLIENT_SECRET"].strip()
REFRESH_TOKEN = os.environ["SPOTIFY_REFRESH_TOKEN"].strip()

LAST_CLONE_FILE = "last_clone_id.txt"


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


def get_all_track_uris(sp: spotipy.Spotify, playlist_id: str) -> list[str]:
    uris: list[str] = []
    results = sp.playlist_items(playlist_id, additional_types=["track", "episode"])

    while results:
        for item in results.get("items", []):
            track = item.get("track")
            if not track:
                continue

            uri = track.get("uri")
            if uri and uri.startswith("spotify:track:"):
                uris.append(uri)

        results = sp.next(results) if results.get("next") else None

    return uris


def read_last_clone_id() -> str | None:
    if not os.path.exists(LAST_CLONE_FILE):
        return None
    with open(LAST_CLONE_FILE, "r", encoding="utf-8") as f:
        v = f.read().strip()
    return v or None


def write_last_clone_id(pid: str) -> None:
    with open(LAST_CLONE_FILE, "w", encoding="utf-8") as f:
        f.write(pid)


def delete_playlist_by_id(sp: spotipy.Spotify, playlist_id: str) -> None:
    # Safety: never delete the source
    if playlist_id == SOURCE_PLAYLIST_ID:
        return
    sp.current_user_unfollow_playlist(playlist_id)
    print(f"Deleted previous clone: {playlist_id}")


def copy_playlist_cover(sp: spotipy.Spotify, source_id: str, target_id: str) -> None:
    images = sp.playlist_cover_image(source_id)
    if not images:
        print("No cover image found on source playlist, skipping.")
        return

    image_url = images[0]["url"]
    r = requests.get(image_url, timeout=30)
    r.raise_for_status()

    # Spotify requires base64 JPEG <= 256KB. Most Spotify-hosted covers already comply.
    image_bytes = r.content

    if len(image_bytes) > 256_000:
        raise RuntimeError(f"Cover image too large for Spotify upload ({len(image_bytes)} bytes).")

    image_b64 = base64.b64encode(image_bytes).decode("utf-8")
    sp.playlist_upload_cover_image(target_id, image_b64)
    print("Copied cover image.")


def main():
    sp = spotify_client()
    user_id = sp.current_user()["id"]

    # Delete previous clone if we know it
    old_clone_id = read_last_clone_id()
    if old_clone_id:
        try:
            delete_playlist_by_id(sp, old_clone_id)
        except Exception as e:
            print(f"Failed to delete old clone (non-fatal): {e}")

    # Read source playlist (original is never modified)
    source_playlist = sp.playlist(SOURCE_PLAYLIST_ID)
    source_name = source_playlist["name"]

    # Create new clone with SAME NAME and BLANK description
    track_uris = get_all_track_uris(sp, SOURCE_PLAYLIST_ID)

    new_playlist = sp.user_playlist_create(
        user=user_id,
        name=source_name,
        public=False,
        description="",
    )

    for i in range(0, len(track_uris), 100):
        sp.playlist_add_items(new_playlist["id"], track_uris[i:i + 100])

    print(f"Created new clone: {new_playlist['id']}")

    # Best-effort cover copy
    try:
        copy_playlist_cover(sp, SOURCE_PLAYLIST_ID, new_playlist["id"])
    except Exception as e:
        print(f"Cover copy failed (non-fatal): {e}")

    # Update files for Pages + next deletion
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

    write_last_clone_id(new_playlist["id"])


if __name__ == "__main__":
    main()

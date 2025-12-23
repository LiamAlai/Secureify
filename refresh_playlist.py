import os
import requests
import spotipy

SOURCE_PLAYLIST_ID = os.environ["SOURCE_PLAYLIST_ID"]
CACHE_FILE = "last_clone.txt"

CLIENT_ID = os.environ["SPOTIFY_CLIENT_ID"]
CLIENT_SECRET = os.environ["SPOTIFY_CLIENT_SECRET"]
REFRESH_TOKEN = os.environ["SPOTIFY_REFRESH_TOKEN"]

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
    results = sp.playlist_items(playlist_id, additional_types=["track"])

    while results:
        for item in results.get("items", []):
            track = item.get("track")
            if track and track.get("uri"):
                uris.append(track["uri"])
        results = sp.next(results) if results.get("next") else None

    return uris

def safe_delete_playlist(sp: spotipy.Spotify, playlist_id: str) -> None:
    # Make private (best effort)
    try:
        sp.playlist_change_details(playlist_id, public=False)
    except Exception:
        pass

    # Remove all tracks (best effort)
    try:
        old_uris = get_all_track_uris(sp, playlist_id)
        for i in range(0, len(old_uris), 100):
            sp.playlist_remove_all_occurrences_of_items(playlist_id, old_uris[i:i + 100])
    except Exception:
        pass

    # Unfollow so it disappears from your library
    sp.current_user_unfollow_playlist(playlist_id)

def main():
    sp = spotify_client()
    user_id = sp.current_user()["id"]

    # Delete previous clone if we have it
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            old_id = f.read().strip()
        if old_id:
            safe_delete_playlist(sp, old_id)

    # Copy source playlist
source_playlist = sp.playlist(SOURCE_PLAYLIST_ID)
source_name = source_playlist["name"]

track_uris = get_all_track_uris(sp, SOURCE_PLAYLIST_ID)

new_playlist = sp.user_playlist_create(
    user=user_id,
    name=source_name,
    public=False,
    description="Auto-refreshed every 24 hours (GitHub Actions)",
)

    for i in range(0, len(track_uris), 100):
        sp.playlist_add_items(new_playlist["id"], track_uris[i:i + 100])

    # Save new clone ID for next run
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        f.write(new_playlist["id"])

    print(f"Refreshed clone playlist: {new_playlist['id']}")

if __name__ == "__main__":
    main()

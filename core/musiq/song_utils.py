"""This module provides some utility functions concerning songs."""

import os
import re
from typing import TYPE_CHECKING

import mutagen.easymp4

from main import settings

if TYPE_CHECKING:
    from typing_extensions import TypedDict
    from core.musiq.music_provider import ArchivedPlaylist
    from core.musiq.musiq import Musiq

    Metadata = TypedDict(  # pylint: disable=invalid-name
        "Metadata",
        {
            "artist": str,
            "title": str,
            "duration": float,
            "internal_url": str,
            "external_url": str,
            "stream_url": str,  # optional
        },
        total=False,
    )


def get_path(basename: str) -> str:
    """Returns the absolute path for a basename of a file in the cache directory."""
    path = os.path.join(settings.SONGS_CACHE_DIR, basename)
    path = path.replace("~", os.environ["HOME"])
    path = os.path.abspath(path)
    return path


def determine_url_type(url: str) -> str:
    """Returns the service the given url corresponds to."""
    if url.startswith("local_library/"):
        return "local"
    if url.startswith("https://www.youtube.com/"):
        return "youtube"
    if url.startswith("https://open.spotify.com/"):
        return "spotify"
    if url.startswith("https://soundcloud.com/"):
        return "soundcloud"
    if url.startswith("https://www.jamendo.com/"):
        return "jamendo"
    return "unknown"


def determine_playlist_type(archived_playlist: "ArchivedPlaylist") -> str:
    """Uses the url of the first song in the playlist
    to determine the platform where the playlist is from."""
    if archived_playlist.list_id.startswith("playlog"):
        # The playlist was created from play logs and may contain various song types.
        return "playlog"
    first_song = archived_playlist.entries.first()
    if not first_song:
        raise ValueError("Playlist contains no songs.")
    first_song_url = first_song.url
    return determine_url_type(first_song_url)


def format_seconds(seconds: int) -> str:
    """Takes seconds and formats them as [hh:]mm:ss."""

    if seconds < 0:
        return "--:--"

    hours, seconds = seconds // 3600, seconds % 3600
    minutes, seconds = seconds // 60, seconds % 60

    formatted = ""
    if hours > 0:
        formatted += "{:d}:".format(int(hours))
    formatted += "{0:02d}:{1:02d}".format(int(minutes), int(seconds))
    return formatted


def displayname(artist: str, title: str) -> str:
    """Formats the given artist and title as a presentable display name."""
    if artist == "":
        return title
    return artist + " – " + title


def get_metadata(path: str) -> "Metadata":
    """gathers the metadata for the song at the given location.
    'title' and 'duration' is read from tags, the 'url' is built from the location"""

    parsed = mutagen.File(path, easy=True)
    if parsed is None:
        raise ValueError
    metadata: "Metadata" = {}

    if parsed.tags is not None:
        if "artist" in parsed.tags:
            metadata["artist"] = parsed.tags["artist"][0]
        if "title" in parsed.tags:
            metadata["title"] = parsed.tags["title"][0]
    if "artist" not in metadata:
        metadata["artist"] = ""
    if "title" not in metadata:
        metadata["title"] = os.path.split(path)[1]
    if parsed.info is not None and parsed.info.length is not None:
        metadata["duration"] = parsed.info.length
    else:
        metadata["duration"] = -1

    return metadata


def is_forbidden(musiq: "Musiq", s: str) -> bool:
    # We can't access the variable in settings/basic.py
    # since we are in a static context without a reference to bes
    keywords = musiq.base.settings.basic.forbidden_keywords
    words = re.split(r"[,\s]+", keywords.strip())
    # delete empty matches
    words = [word for word in words if word]

    for word in words:
        if re.search(word, s, re.IGNORECASE):
            return True
    return False

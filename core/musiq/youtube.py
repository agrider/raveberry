"""This module contains all Youtube related code."""

from __future__ import annotations

import errno
import json
import logging
import os
import pickle
import shutil
import subprocess
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, TYPE_CHECKING, Iterator, cast
from urllib.parse import parse_qs
from urllib.parse import urlparse

import requests
import youtube_dl
from django.conf import settings
from django.http.response import HttpResponse

import core.musiq.song_utils as song_utils
from core.musiq.song_provider import SongProvider
from core.musiq.playlist_provider import PlaylistProvider

if TYPE_CHECKING:
    from core.musiq.musiq import Musiq
    from core.musiq.song_utils import Metadata


@contextmanager
def youtube_session() -> Iterator[requests.Session]:
    """This context opens a requests session and loads the youtube cookies file."""
    session = requests.session()

    pickle_file = os.path.join(settings.BASE_DIR, "config/youtube_cookies.pickle")

    session = requests.session()
    # Have youtube-dl deal with consent cookies etc to setup a valid session
    extractor = youtube_dl.extractor.youtube.YoutubeIE()
    extractor._downloader = youtube_dl.YoutubeDL()
    extractor.initialize()
    session.cookies.update(extractor._downloader.cookiejar)

    try:
        if os.path.getsize(pickle_file) > 0:
            with open(pickle_file, "rb") as f:
                session.cookies.update(pickle.load(f))
    except FileNotFoundError:
        pass

    headers = {"User-Agent": youtube_dl.utils.random_user_agent()}
    session.headers.update(headers)
    yield session

    with open(pickle_file, "wb") as f:
        pickle.dump(session.cookies, f)


class YoutubeDLLogger:
    """This logger class is used to log process of youtube-dl downloads."""

    @classmethod
    def debug(cls, msg: str) -> None:
        """This method is called if youtube-dl does debug level logging."""
        logging.debug(msg)

    @classmethod
    def warning(cls, msg: str) -> None:
        """This method is called if youtube-dl does warning level logging."""
        logging.debug(msg)

    @classmethod
    def error(cls, msg: str) -> None:
        """This method is called if youtube-dl does error level logging."""
        logging.error(msg)


class Youtube:
    """This class contains code for both the song and playlist provider"""

    atomicparsley_available = shutil.which("AtomicParsley") is not None

    @staticmethod
    def get_ydl_opts() -> Dict[str, Any]:
        """This method returns a dictionary containing sensible defaults for youtube-dl options.
        It is roughly equivalent to the following command:
        youtube-dl --format bestaudio[ext=m4a]/best[ext=m4a] --output '%(id)s.%(ext)s' \
            --no-playlist --no-cache-dir --write-thumbnail --default-search auto \
            --add-metadata --embed-thumbnail
        """
        postprocessors = [{"key": "FFmpegMetadata"}]
        if Youtube.atomicparsley_available:
            postprocessors.append(
                {
                    "key": "EmbedThumbnail",
                    # overwrite any thumbnails already present
                    "already_have_thumbnail": True,
                }
            )
        return {
            "format": "bestaudio[ext=m4a]/best[ext=m4a]",
            "outtmpl": os.path.join(settings.SONGS_CACHE_DIR, "%(id)s.%(ext)s"),
            "noplaylist": True,
            "cachedir": False,
            "no_color": True,
            "writethumbnail": True,
            "default_search": "auto",
            "postprocessors": postprocessors,
            "logger": YoutubeDLLogger(),
        }

    @staticmethod
    def _get_initial_data(html: str) -> Dict[str, Any]:
        for line in html.split("\n"):
            line = line.strip()
            before = "var ytInitialData = "
            after = ";</"
            if before in line:
                # extract json
                line = line[line.index(before) + len(before) :]
                initial_data = line[: line.index(after)]
                return json.loads(initial_data)
        raise ValueError("Could not parse initial data from html")

    @staticmethod
    def get_search_suggestions(musiq: Musiq, query: str) -> List[str]:
        """Returns a list of suggestions for the given query from Youtube."""
        with youtube_session() as session:
            params = {
                "client": "youtube",
                "q": query[:100],  # queries longer than 100 characters are not accepted
                "xhr": "t",  # this makes the response be a json file
            }
            response = session.get(
                "https://clients1.google.com/complete/search", params=params
            )
        suggestions = json.loads(response.text)
        # first entry is the query, the second one contains the suggestions
        suggestions = suggestions[1]
        # suggestions are given as tuples
        # extract the string and skip the query if it occurs identically
        suggestions = [
            entry[0]
            for entry in suggestions
            if entry[0] != query and not song_utils.is_forbidden(musiq, entry[0])
        ]
        return suggestions


class YoutubeSongProvider(SongProvider, Youtube):
    """This class handles songs from Youtube."""

    @staticmethod
    def get_id_from_external_url(url: str) -> str:
        return parse_qs(urlparse(url).query)["v"][0]

    def __init__(
        self, musiq: "Musiq", query: Optional[str], key: Optional[int]
    ) -> None:
        self.type = "youtube"
        super().__init__(musiq, query, key)
        self.info_dict: Dict[str, Any] = {}
        self.ydl_opts = Youtube.get_ydl_opts()

    def check_cached(self) -> bool:
        if not self.id:
            return False
        return os.path.isfile(self._get_path())

    def check_available(self) -> bool:

        # directly use the search extractors entry function so we can process each result
        # as soon as it's available instead of waiting for all of them
        extractor = youtube_dl.extractor.youtube.YoutubeSearchIE()
        extractor._downloader = youtube_dl.YoutubeDL(self.ydl_opts)
        extractor.initialize()
        for entry in extractor._entries(self.query, 50):
            if song_utils.is_forbidden(self.musiq, entry["title"]):
                continue
            try:
                with youtube_dl.YoutubeDL(self.ydl_opts) as ydl:
                    self.info_dict = ydl.extract_info(entry["id"], download=False)
                break
            except (
                youtube_dl.utils.ExtractorError,
                youtube_dl.utils.DownloadError,
            ) as e:
                logging.warning("error during availability check for %s:", entry["id"])
                logging.warning(e)
        else:
            self.error = "No songs found"
            return False

        self.id = self.info_dict["id"]

        return self.check_not_too_large(self.info_dict["filesize"])

    def _download(self) -> bool:
        error = None
        location = None

        try:
            with youtube_dl.YoutubeDL(self.ydl_opts) as ydl:
                ydl.download([self.get_external_url()])

            location = self._get_path()
            base = os.path.splitext(location)[0]
            thumbnail = base + ".jpg"
            try:
                os.remove(thumbnail)
            except FileNotFoundError:
                logging.info("tried to delete %s but does not exist", thumbnail)

            try:
                # tag the file with replaygain to perform volume normalization
                subprocess.call(
                    ["rganalysis", location],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except OSError as e:
                if e.errno == errno.ENOENT:
                    pass  # the rganalysis package was not found. Skip normalization
                else:
                    raise

        except youtube_dl.utils.DownloadError as e:
            error = e

        if error is not None or location is None:
            logging.error("accessible video could not be downloaded: %s", self.id)
            logging.error("location: %s", location)
            logging.error(error)
            return False
        return True

    def make_available(self) -> bool:
        if not os.path.isfile(self._get_path()):
            self.musiq.update_state()
            # only download the file if it was not already downloaded
            return self._download()
        return True

    def get_metadata(self) -> "Metadata":
        if not self.id:
            raise ValueError()
        metadata = song_utils.get_metadata(self._get_path())

        metadata["internal_url"] = self.get_internal_url()
        metadata["external_url"] = "https://www.youtube.com/watch?v=" + self.id
        if not metadata["title"]:
            metadata["title"] = metadata["external_url"]

        return metadata

    def _get_path(self) -> str:
        if not self.id:
            raise ValueError()
        return song_utils.get_path(self.id + ".m4a")

    def get_internal_url(self) -> str:
        return "file://" + self._get_path()

    def get_external_url(self) -> str:
        if not self.id:
            raise ValueError()
        return "https://www.youtube.com/watch?v=" + self.id

    def get_suggestion(self) -> str:
        with youtube_session() as session:
            response = session.get(self.get_external_url())

        initial_data = Youtube._get_initial_data(response.text)

        path = [
            "contents",
            "twoColumnWatchNextResults",
            "autoplay",
            "autoplay",
            "sets",
            0,
            "autoplayVideo",
            "commandMetadata",
            "webCommandMetadata",
            "url",
        ]
        url = initial_data
        for step in path:
            url = url[cast(str, step)]
        return "https://www.youtube.com" + cast(str, url)

    def request_radio(self, request_ip: str) -> HttpResponse:
        if not self.id:
            raise ValueError()
        radio_id = "RD" + self.id

        provider = YoutubePlaylistProvider(self.musiq, "", None)
        provider.id = radio_id
        provider.request("", archive=False, manually_requested=False)
        return HttpResponse("queueing radio (might take some time)")


class YoutubePlaylistProvider(PlaylistProvider, Youtube):
    """This class handles Youtube Playlists."""

    @staticmethod
    def get_id_from_external_url(url: str) -> Optional[str]:
        try:
            list_id = parse_qs(urlparse(url).query)["list"][0]
        except KeyError:
            return None
        return list_id

    def __init__(
        self, musiq: "Musiq", query: Optional[str], key: Optional[int]
    ) -> None:
        self.type = "youtube"
        super().__init__(musiq, query, key)
        self.ydl_opts = Youtube.get_ydl_opts()
        del self.ydl_opts["noplaylist"]
        self.ydl_opts["extract_flat"] = True

    def is_radio(self) -> bool:
        if not self.id:
            raise ValueError()
        return self.id.startswith("RD")

    def search_id(self) -> Optional[str]:
        with youtube_session() as session:
            params = {
                "search_query": self.query,
                # this is the value that youtube uses to filter for playlists only
                "sp": "EgQQA1AD",
            }
            response = session.get("https://www.youtube.com/results", params=params)

        initial_data = Youtube._get_initial_data(response.text)

        path = [
            "contents",
            "twoColumnSearchResultsRenderer",
            "primaryContents",
            "sectionListRenderer",
            "contents",
        ]
        section_renderers = initial_data
        for step in path:
            section_renderers = section_renderers[step]

        list_id = None
        for section_renderer in cast(List[Dict[str, Any]], section_renderers):
            search_results = section_renderer["itemSectionRenderer"]["contents"]

            try:
                list_id = next(
                    res["playlistRenderer"]["playlistId"]
                    for res in search_results
                    if "playlistRenderer" in res
                )
                break
            except StopIteration:
                # the search result did not contain the list id
                pass

        return list_id

    def fetch_metadata(self) -> bool:
        # in case of a radio playlist, restrict the number of songs that are downloaded
        if self.is_radio():
            self.ydl_opts[
                "playlistend"
            ] = self.musiq.base.settings.basic.max_playlist_items
            # radios are not viewable with the /playlist?list= url,
            # create a video watch url with the radio list
            query_url = (
                "https://www.youtube.com/watch?v=" + self.id[2:] + "&list=" + self.id
            )
        else:
            # if only given the id, youtube-dl returns an info dict resolving this id to a url.
            # we want to receive the playlist entries directly, so we query the playlist url
            query_url = "https://www.youtube.com/playlist?list=" + self.id

        try:
            with youtube_dl.YoutubeDL(self.ydl_opts) as ydl:
                info_dict = ydl.extract_info(query_url, download=False)
        except (youtube_dl.utils.ExtractorError, youtube_dl.utils.DownloadError) as e:
            self.error = e
            return False

        if info_dict["_type"] != "playlist" or "entries" not in info_dict:
            # query was not a playlist url -> search for the query
            assert False

        assert self.id == info_dict["id"]
        if "title" in info_dict:
            self.title = info_dict["title"]
        for entry in info_dict["entries"]:
            self.urls.append("https://www.youtube.com/watch?v=" + entry["id"])
        assert self.key is None

        return True

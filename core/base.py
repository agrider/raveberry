"""This module provides common functionality for all pages on the site."""

import os
import random
from typing import Dict, Any, List

from django.conf import settings
from django.core.handlers.wsgi import WSGIRequest
from django.db import transaction
from django.http import HttpResponseBadRequest
from django.http import HttpResponseRedirect
from django.http.response import HttpResponse, JsonResponse
from django.shortcuts import render
from django.urls import reverse, URLPattern

import core.models as models
from core.lights.lights import Lights
from core.api import Api
from core.musiq.musiq import Musiq
from core.network_info import NetworkInfo
from core.settings.settings import Settings
from core.state_handler import Stateful
from core.user_manager import UserManager


class Base(Stateful):
    """This class contains methods that are needed by all pages."""

    def __init__(self) -> None:
        self.urlpatterns: List[URLPattern] = []
        self.settings = Settings(self)
        self.user_manager = UserManager(self)
        self.lights = Lights(self)
        self.musiq = Musiq(self)
        self.network_info = NetworkInfo(self)
        self.api = Api(self)

    def start(self) -> None:
        self.lights.start()
        self.musiq.start()

    @classmethod
    def _get_random_hashtag(cls) -> str:
        active_hashtags = models.Tag.objects.filter(active=True)
        if active_hashtags.count() == 0:
            return "no hashtags present :("
        index = random.randint(0, active_hashtags.count() - 1)
        hashtag = active_hashtags[index]
        return hashtag.text

    @classmethod
    def _get_apk_link(cls) -> str:
        local_apk = os.path.join(settings.STATIC_FILES, "apk/shareberry.apk")
        if os.path.isfile(local_apk):
            return os.path.join(settings.STATIC_URL, "apk/shareberry.apk")
        return "https://github.com/raveberry/shareberry/releases/latest/download/shareberry.apk"

    def _increment_counter(self) -> int:
        with transaction.atomic():
            counter = models.Counter.objects.get_or_create(id=1, defaults={"value": 0})[
                0
            ]
            counter.value += 1
            counter.save()
        self.update_state()
        return counter.value

    def context(self, request: WSGIRequest) -> Dict[str, Any]:
        """Returns the base context that is needed on every page.
        Increments the visitors counter."""
        self._increment_counter()
        return {
            "base_urls": self.urlpatterns,
            "voting_system": self.settings.basic.voting_system,
            "hashtag": self._get_random_hashtag(),
            "demo": settings.DEMO,
            "controls_enabled": self.user_manager.has_controls(request.user),
            "is_admin": self.user_manager.is_admin(request.user),
            "apk_link": self._get_apk_link(),
            "local_enabled": self.settings.platforms.local_enabled,
            "youtube_enabled": self.settings.platforms.youtube_enabled,
            "spotify_enabled": self.settings.platforms.spotify_enabled,
            "soundcloud_enabled": self.settings.platforms.soundcloud_enabled,
            "jamendo_enabled": self.settings.platforms.jamendo_enabled,
            "streaming_enabled": settings.DOCKER_ICECAST
            or self.settings.sound.output == "icecast",
        }

    def state_dict(self) -> Dict[str, Any]:
        # this function constructs a base state dictionary with website wide state
        # pages sending states extend this state dictionary
        return {
            "partymode": self.user_manager.partymode_enabled(),
            "users": self.user_manager.get_count(),
            "visitors": models.Counter.objects.get_or_create(
                id=1, defaults={"value": 0}
            )[0].value,
            "lightsEnabled": self.lights.loop_active.is_set(),
            "alarm": self.musiq.playback.alarm_playing.is_set(),
            "defaultPlatform": "spotify"
            if self.settings.platforms.spotify_enabled
            else "youtube",
        }

    def no_stream(self, request: WSGIRequest) -> HttpResponse:
        """Renders the /stream page. If this is reached, there is no stream active."""
        context = self.context(request)
        return render(request, "no_stream.html", context)

    def submit_hashtag(self, request: WSGIRequest) -> HttpResponse:
        """Add the given hashtag to the database."""
        hashtag = request.POST.get("hashtag")
        if hashtag is None or len(hashtag) == 0:
            return HttpResponseBadRequest()

        if hashtag[0] != "#":
            hashtag = "#" + hashtag
        models.Tag.objects.create(
            text=hashtag, active=self.settings.basic.hashtags_active
        )

        return HttpResponse()

    def logged_in(self, request: WSGIRequest) -> HttpResponse:
        """This endpoint is visited after every login.
        Redirect the admin to the settings and everybody else to the musiq page."""
        if self.user_manager.is_admin(request.user):
            return HttpResponseRedirect(reverse("settings"))
        return HttpResponseRedirect(reverse("musiq"))

    @Lights.option
    def set_lights_shortcut(self, request: WSGIRequest) -> None:
        return self.lights.controller._set_lights_shortcut(request)

    @Settings.option
    def upgrade_available(self, _request: WSGIRequest) -> HttpResponse:
        latest_version = self.settings.system._fetch_latest_version()
        current_version = settings.VERSION
        if latest_version and latest_version != current_version:
            return JsonResponse(True, safe=False)
        return JsonResponse(False, safe=False)

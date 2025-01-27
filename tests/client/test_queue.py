import json

from django.urls import reverse

from tests.music_test import MusicTest


class QueueTests(MusicTest):
    def setUp(self):
        super().setUp()
        self._setup_test_library()
        self._add_local_playlist()

    def test_add(self):
        suggestion = json.loads(
            self.client.get(reverse("random-suggestion"), {"playlist": "false"}).content
        )
        self.client.post(
            reverse("request-music"),
            {
                "key": suggestion["key"],
                "query": "",
                "playlist": "false",
                "platform": "local",
            },
        )
        self._poll_musiq_state(lambda state: len(state["musiq"]["songQueue"]) == 4)
        self.client.post(
            reverse("request-music"),
            {
                "key": suggestion["key"],
                "query": "",
                "playlist": "false",
                "platform": "local",
            },
        )
        self._poll_musiq_state(lambda state: len(state["musiq"]["songQueue"]) == 5)

    def test_remove(self):
        state = json.loads(self.client.get(reverse("musiq-state")).content)
        key = state["musiq"]["songQueue"][1]["id"]

        # removing a song shortens the queue by one
        self.client.post(reverse("remove"), {"key": str(key)})
        self._poll_musiq_state(lambda state: len(state["musiq"]["songQueue"]) == 2)

        # removing the same key another time should not change the queue length
        self.client.post(reverse("remove"), {"key": str(key)})
        self._poll_musiq_state(lambda state: len(state["musiq"]["songQueue"]) == 2)

        # choosing a new one should
        key = state["musiq"]["songQueue"][0]["id"]
        self.client.post(reverse("remove"), {"key": str(key)})
        self._poll_musiq_state(lambda state: len(state["musiq"]["songQueue"]) == 1)

    def test_prioritize(self):
        state = json.loads(self.client.get(reverse("musiq-state")).content)
        key = state["musiq"]["songQueue"][1]["id"]

        # the chosen key should now be at the first spot
        self.client.post(reverse("prioritize"), {"key": str(key)})
        self._poll_musiq_state(
            lambda state: state["musiq"]["songQueue"][0]["id"] == key
        )

        # another prioritize will not change this
        self.client.post(reverse("prioritize"), {"key": str(key)})
        self._poll_musiq_state(
            lambda state: state["musiq"]["songQueue"][0]["id"] == key
        )

        # another key will
        key = state["musiq"]["songQueue"][2]["id"]
        self.client.post(reverse("prioritize"), {"key": str(key)})
        self._poll_musiq_state(
            lambda state: state["musiq"]["songQueue"][0]["id"] == key
        )

    def test_reorder(self):
        state = json.loads(self.client.get(reverse("musiq-state")).content)
        # key1 -> key2 -> key3
        key1 = state["musiq"]["songQueue"][0]["id"]
        key2 = state["musiq"]["songQueue"][1]["id"]
        key3 = state["musiq"]["songQueue"][2]["id"]

        # key2 -> key1 -> key3
        # both keys are given
        self.client.post(
            reverse("reorder"),
            {"prev": str(key2), "element": str(key1), "next": str(key3)},
        )
        self._poll_musiq_state(
            lambda state: [song["id"] for song in state["musiq"]["songQueue"]]
            == [key2, key1, key3]
        )

        # key3 -> key2 -> key1
        # only the next key is given (=prioritize)
        self.client.post(
            reverse("reorder"), {"prev": "", "element": str(key3), "next": str(key2)}
        )
        self._poll_musiq_state(
            lambda state: [song["id"] for song in state["musiq"]["songQueue"]]
            == [key3, key2, key1]
        )

        # key2 -> key1 -> key3
        # only the prev key is given (=deprioritize)
        self.client.post(
            reverse("reorder"), {"prev": str(key1), "element": str(key3), "next": ""}
        )
        self._poll_musiq_state(
            lambda state: [song["id"] for song in state["musiq"]["songQueue"]]
            == [key2, key1, key3]
        )

    def test_remove_all(self):
        self.client.post(reverse("remove-all"))
        self._poll_musiq_state(lambda state: len(state["musiq"]["songQueue"]) == 0)


class QueueVotingTests(MusicTest):
    def setUp(self):
        super().setUp()
        self._setup_test_library()
        self._add_local_playlist()

        self.client.post(reverse("set-voting-system"), {"value": "true"})
        self._poll_state(
            "settings-state", lambda state: state["settings"]["votingSystem"] == True
        )
        self.client.logout()

    def test_votes(self):
        state = json.loads(self.client.get(reverse("musiq-state")).content)
        # key1 -> key2 -> key3
        key1 = state["musiq"]["songQueue"][0]["id"]
        key2 = state["musiq"]["songQueue"][1]["id"]
        key3 = state["musiq"]["songQueue"][2]["id"]

        self.client.post(reverse("vote-up"), {"key": str(key2)})
        self.client.post(reverse("vote-up"), {"key": str(key3)})
        self._poll_musiq_state(
            lambda state: [song["id"] for song in state["musiq"]["songQueue"]]
            == [key2, key3, key1]
        )

        self.client.post(reverse("vote-up"), {"key": str(key1)})
        self._poll_musiq_state(
            lambda state: [song["id"] for song in state["musiq"]["songQueue"]]
            == [key1, key2, key3]
        )

        self.client.post(reverse("vote-down"), {"key": str(key2)})
        self.client.post(reverse("vote-down"), {"key": str(key2)})
        self.client.post(reverse("vote-down"), {"key": str(key1)})
        self._poll_musiq_state(
            lambda state: [song["id"] for song in state["musiq"]["songQueue"]]
            == [key3, key1, key2]
        )

    def test_vote_remove(self):
        state = json.loads(self.client.get(reverse("musiq-state")).content)
        # key1 -> key2 -> key3
        key1 = state["musiq"]["songQueue"][0]["id"]
        key2 = state["musiq"]["songQueue"][1]["id"]
        key3 = state["musiq"]["songQueue"][2]["id"]

        for _ in range(3):
            self.client.post(reverse("vote-down"), {"key": str(key2)})
        self._poll_musiq_state(
            lambda state: [song["id"] for song in state["musiq"]["songQueue"]]
            == [key1, key3]
        )

    def test_vote_skip(self):
        state = json.loads(self.client.get(reverse("musiq-state")).content)
        # key1 -> key2 -> key3
        key = state["musiq"]["currentSong"]["queueKey"]

        for _ in range(3):
            self.client.post(reverse("vote-down"), {"key": str(key)})
        self._poll_musiq_state(lambda state: len(state["musiq"]["songQueue"]) == 2)

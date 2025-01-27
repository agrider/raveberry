"""This module handles all settings related to sound output."""
from __future__ import annotations

import re
import subprocess
import time
from typing import Dict, TYPE_CHECKING, Optional, List

from django.core.handlers.wsgi import WSGIRequest
from django.http import HttpResponse
from django.http import HttpResponseBadRequest
from django.http import JsonResponse

from core import util
from core.models import Setting
from core.settings.settings import Settings
from core.util import background_thread


class Sound:
    """This class is responsible for handling settings changes related to sound output."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.output = Settings.get_setting("sound_output", "")
        self.backup_stream = Settings.get_setting("backup_stream", "")
        self.bluetoothctl: Optional[subprocess.Popen[bytes]] = None
        self.bluetooth_devices: List[Dict[str, str]] = []

    @Settings.option
    def set_voting_system(self, request: WSGIRequest) -> None:
        """Enables or disables the voting system based on the given value."""
        enabled = request.POST.get("value") == "true"
        Setting.objects.filter(key="voting_system").update(value=enabled)
        self.voting_system = enabled

    @Settings.option
    def set_backup_stream(self, request: WSGIRequest) -> None:
        """Sets the given internet stream as backup stream."""
        stream = request.POST.get("value")
        Setting.objects.filter(key="backup_stream").update(value=stream)
        self.backup_stream = stream

    def _get_bluetoothctl_line(self) -> str:
        # Note: this variable is not guarded by a lock.
        # But there should only be one admin accessing these bluetooth functions anyway.
        if self.bluetoothctl is None:
            return ""
        assert self.bluetoothctl.stdout
        line = self.bluetoothctl.stdout.readline().decode()
        ansi_escape = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
        line = ansi_escape.sub("", line)
        line = line.strip()
        return line

    def _stop_bluetoothctl(self) -> None:
        if self.bluetoothctl:
            assert self.bluetoothctl.stdin
            self.bluetoothctl.stdin.close()
            self.bluetoothctl.wait()
        self.bluetoothctl = None

    @Settings.option
    def set_bluetooth_scanning(self, request: WSGIRequest) -> HttpResponse:
        """Enables scanning of bluetooth devices."""
        enabled = request.POST.get("value") == "true"
        if enabled:
            if self.bluetoothctl is not None:
                return HttpResponseBadRequest("Already Scanning")
            self.bluetooth_devices = []
            self.bluetoothctl = subprocess.Popen(
                ["bluetoothctl"], stdin=subprocess.PIPE, stdout=subprocess.PIPE
            )
            assert self.bluetoothctl.stdin

            self.bluetoothctl.stdin.write(b"devices\n")
            self.bluetoothctl.stdin.write(b"scan on\n")
            self.bluetoothctl.stdin.flush()

            @background_thread
            def do_scan():
                while True:
                    line = self._get_bluetoothctl_line()
                    if not line:
                        break
                    # match old devices
                    match = re.match(r"Device (\S*) (.*)", line)
                    # match newly scanned devices
                    # We need the '.*' at the beginning of the line to account for control sequences
                    if not match:
                        match = re.match(r".*\[NEW\] Device (\S*) (.*)", line)
                    if match:
                        address = match.group(1)
                        name = match.group(2)
                        # filter unnamed devices
                        # devices named after their address are no speakers
                        if re.match("[A-Z0-9][A-Z0-9](-[A-Z0-9][A-Z0-9]){5}", name):
                            continue
                        self.bluetooth_devices.append(
                            {"address": address, "name": name}
                        )
                        self.settings.update_state()

            do_scan()
            return HttpResponse("Started scanning")
        else:
            if self.bluetoothctl is None:
                return HttpResponseBadRequest("Currently not scanning")
            self._stop_bluetoothctl()
            return HttpResponse("Stopped scanning")

    @Settings.option
    def connect_bluetooth(self, request: WSGIRequest) -> HttpResponse:
        """Connect to a given bluetooth device."""
        address = request.POST.get("address")
        if self.bluetoothctl is not None:
            return HttpResponseBadRequest("Stop scanning before connecting")
        if address is None or address == "":
            return HttpResponseBadRequest("No device selected")

        self.bluetoothctl = subprocess.Popen(
            ["bluetoothctl"], stdin=subprocess.PIPE, stdout=subprocess.PIPE
        )
        assert self.bluetoothctl.stdin
        error = ""

        # A Function that acts as a timeout for unexpected errors (or timeouts)
        @background_thread
        def _timeout() -> None:
            nonlocal error
            time.sleep(20)
            error = "Timed out"
            if self.bluetoothctl is not None:
                self._stop_bluetoothctl()

        # Sometimes, pairing hangs forever. Since connecting alone is enough, skip pairing.
        # self.bluetoothctl.stdin.write(b"pair " + address.encode() + b"\n")
        # self.bluetoothctl.stdin.flush()
        # while True:
        #     line = self._get_bluetoothctl_line()
        #     if not line:
        #         break
        #     if re.match(".*Device " + address + " not available", line):
        #         error = "Device unavailable"
        #         break
        #     if re.match(".*Failed to pair: org.bluez.Error.AlreadyExists", line):
        #         break
        #     if re.match(".*Pairing successful", line):
        #         break

        # if error:
        #     self._stop_bluetoothctl()
        #     return HttpResponseBadRequest(error)

        self.bluetoothctl.stdin.write(b"connect " + address.encode() + b"\n")
        self.bluetoothctl.stdin.flush()
        while True:
            line = self._get_bluetoothctl_line()
            if not line:
                break
            if re.match(".*Device " + address + " not available", line):
                error = "Device unavailable"
                break
            if re.match(".*Failed to connect: org.bluez.Error.Failed", line):
                error = "Connect Failed"
                break
            if re.match(".*Failed to connect: org.bluez.Error.InProgress", line):
                error = "Connect in progress"
                break
            if re.match(".*Connection successful", line):
                break
        # trust the device to automatically reconnect when it is available again
        self.bluetoothctl.stdin.write(b"trust " + address.encode() + b"\n")
        self.bluetoothctl.stdin.flush()

        self._stop_bluetoothctl()
        if error:
            return HttpResponseBadRequest(error)

        return HttpResponse("Connected. Set output device to activate.")

    @Settings.option
    def disconnect_bluetooth(self, request: WSGIRequest) -> HttpResponse:
        """Untrusts a given bluetooth device to prevent automatic reconnects.
        Does not unpair or remove the device."""
        address = request.POST.get("address")
        if self.bluetoothctl is not None:
            return HttpResponseBadRequest("Stop scanning before disconnecting")
        if address is None or address == "":
            return HttpResponseBadRequest("No device selected")

        self.bluetoothctl = subprocess.Popen(
            ["bluetoothctl"], stdin=subprocess.PIPE, stdout=subprocess.PIPE
        )
        assert self.bluetoothctl.stdin
        error = ""

        self.bluetoothctl.stdin.write(b"untrust " + address.encode() + b"\n")
        self.bluetoothctl.stdin.flush()
        while True:
            line = self._get_bluetoothctl_line()
            if not line:
                break
            if re.match(".*Device " + address + " not available", line):
                error = "Device unavailable"
                break
            if re.match(".*untrust succeeded", line):
                break

        self._stop_bluetoothctl()
        if error:
            return HttpResponseBadRequest(error)
        return HttpResponse("Disconnected")

    @Settings.option
    def list_outputs(self, _request: WSGIRequest) -> JsonResponse:
        """Returns a list of all sound output devices currently available."""
        output = subprocess.check_output(
            "pactl list short sinks".split(),
            env={"PULSE_SERVER": "127.0.0.1"},
            universal_newlines=True,
        )
        tokenized_lines = [line.split() for line in output.splitlines()]

        sinks = ["fakesink", "icecast", "snapcast"]
        sinks.extend([sink[1] for sink in tokenized_lines if len(sink) >= 2])

        return JsonResponse(sinks, safe=False)

    def _set_output(self, output: str) -> HttpResponse:
        icecast_installed = util.service_installed("icecast2")
        snapcast_installed = util.service_installed("snapserver")

        if output == "fakesink":
            mopidy_output = "fakesink"
        elif output == "icecast":
            if not icecast_installed:
                return HttpResponseBadRequest("Please install icecast2")

            subprocess.call(["sudo", "/usr/local/sbin/raveberry/enable_icecast"])
            mopidy_output = "icecast"
        elif output == "snapcast":
            if not snapcast_installed:
                return HttpResponseBadRequest("Please install snapserver")

            subprocess.call(["sudo", "/usr/local/sbin/raveberry/enable_snapcast"])
            mopidy_output = "snapcast"
        else:
            try:
                subprocess.run(
                    ["pactl", "set-default-sink", output],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    env={"PULSE_SERVER": "127.0.0.1"},
                    check=True,
                )
                mopidy_output = "pulse"
            except subprocess.CalledProcessError as e:
                return HttpResponseBadRequest(e.stderr)

        if icecast_installed and output != "icecast":
            subprocess.call(["sudo", "/usr/local/sbin/raveberry/disable_icecast"])
        if snapcast_installed and output != "snapcast":
            subprocess.call(["sudo", "/usr/local/sbin/raveberry/disable_snapcast"])

        self.settings.system.update_mopidy_config(mopidy_output)

        return HttpResponse(
            "Output was set. Restarting the current song might be necessary."
        )

    @Settings.option
    def set_output(self, request: WSGIRequest) -> HttpResponse:
        """Sets the given device as default output device."""
        output = request.POST.get("value")
        if not output:
            return HttpResponseBadRequest("No output selected")

        if output == self.output:
            return HttpResponseBadRequest("Output unchanged")

        Setting.objects.filter(key="sound_output").update(value=output)
        self.output = output

        return self._set_output(output)

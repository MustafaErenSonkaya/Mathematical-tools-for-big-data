"""
Notifiers: deliver AlertEvents somewhere (console, file, email, chat...).

Every notifier implements the same tiny interface, `send(event)`. The rest of
the system only knows about that interface, so adding a new channel means
writing one new class, with no changes anywhere else. Example for later:

    class TelegramNotifier(Notifier):
        def __init__(self, bot_token, chat_id):
            self.url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            self.chat_id = chat_id

        def send(self, event):
            text = f"{event.kind} {event.sensor} peak_z={event.peak_z:+.1f}"
            urllib.request.urlopen(self.url, data=urllib.parse.urlencode(
                {"chat_id": self.chat_id, "text": text}).encode())

Then add it to the list built in main.py.
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path

from src.alerts import AlertEvent


class Notifier(ABC):
    """Base class: anything that can receive an AlertEvent."""

    @abstractmethod
    def send(self, event: AlertEvent) -> None: ...

    def close(self) -> None:
        """Release resources (files, connections). Optional."""


class ConsoleNotifier(Notifier):
    """Prints events with ANSI colors: red for ALERT, green for RESOLVED."""

    RED = "\033[91m"
    GREEN = "\033[92m"
    BOLD = "\033[1m"
    RESET = "\033[0m"

    def __init__(self, use_colors: bool = True):
        self.use_colors = use_colors
        if use_colors and os.name == "nt":
            # Trick: running any shell command makes Windows enable ANSI
            # escape-code processing for this console.
            os.system("")

    def _color(self, text: str, color: str) -> str:
        return f"{self.BOLD}{color}{text}{self.RESET}" if self.use_colors else text

    def format(self, e: AlertEvent) -> str:
        when = datetime.fromtimestamp(e.timestamp).strftime("%Y-%m-%d %H:%M:%S")
        if e.kind == "ALERT":
            head = self._color("ALERT   ", self.RED)
            body = f"z={e.z:+.2f} value={e.value:.2f}" if e.value is not None else f"z={e.z:+.2f}"
        else:
            head = self._color("RESOLVED", self.GREEN)
            body = f"duration={e.duration_seconds:.0f}s"
        return f"[{when}] {head} {e.sensor:<9} id={e.incident_id}  {body}  peak_z={e.peak_z:+.2f}"

    def send(self, event: AlertEvent) -> None:
        print(self.format(event), flush=True)


class JsonlNotifier(Notifier):
    """
    Appends one JSON object per line ("JSON Lines"). Easy to grep, easy to
    load with pandas, and appending never corrupts earlier lines.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("a", encoding="utf-8")

    def send(self, event: AlertEvent) -> None:
        self._file.write(json.dumps(event.to_dict()) + "\n")
        self._file.flush()  # make it visible immediately (e.g. to `tail -f`)

    def close(self) -> None:
        self._file.close()


class NotifierGroup(Notifier):
    """
    Sends each event to several notifiers. One failing notifier (say, the
    email server is down) must not stop the others, so errors are caught and
    reported per notifier.
    """

    def __init__(self, notifiers: list[Notifier]):
        self.notifiers = notifiers

    def send(self, event: AlertEvent) -> None:
        for n in self.notifiers:
            try:
                n.send(event)
            except Exception as exc:  # noqa: BLE001 - keep the pipeline alive
                print(f"[notifier error] {type(n).__name__}: {exc}")

    def close(self) -> None:
        for n in self.notifiers:
            n.close()

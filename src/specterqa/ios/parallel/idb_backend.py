"""M15: IdbInputBackend — idb-based multi-device touch and keyboard input.

Wraps the ``idb`` command-line tool to drive UI interactions on a specific iOS
simulator identified by UDID. Used in multi-simulator sessions where CGEvents
cannot distinguish between devices.
"""

from __future__ import annotations

import shutil
import subprocess


class IdbInputBackend:
    """Send UI input events to an iOS simulator via ``idb``.

    Args:
        udid: The UDID of the target simulator.
    """

    def __init__(self, udid: str) -> None:
        self.udid: str = udid

    # ------------------------------------------------------------------
    # Class-level availability check
    # ------------------------------------------------------------------

    @classmethod
    def is_available(cls) -> bool:
        """Return ``True`` if the ``idb`` binary is present on ``PATH``."""
        return shutil.which("idb") is not None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _run(self, cmd: list) -> subprocess.CompletedProcess:
        """Execute *cmd* via subprocess.

        Raises:
            RuntimeError: If the ``idb`` binary is not found (``FileNotFoundError``),
                with a human-readable install hint that mentions "idb".
        """
        try:
            return subprocess.run(cmd, capture_output=True, check=False)
        except FileNotFoundError:
            raise RuntimeError(
                "idb is not installed or not on PATH. "
                "Install it with: pip install fb-idb  (or brew install idb-companion). "
                "See https://fbidb.io for full setup instructions."
            )

    # ------------------------------------------------------------------
    # Public input actions
    # ------------------------------------------------------------------

    def tap(self, x: float, y: float) -> None:
        """Tap at the given screen coordinates.

        Args:
            x: Horizontal coordinate in points.
            y: Vertical coordinate in points.

        Raises:
            RuntimeError: If ``idb`` is not installed.
        """
        self._run(["idb", "ui", "tap", "--udid", self.udid, str(x), str(y)])

    def swipe(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        duration: float = 0.3,
    ) -> None:
        """Swipe from (x1, y1) to (x2, y2) over *duration* seconds.

        Args:
            x1: Start x coordinate.
            y1: Start y coordinate.
            x2: End x coordinate.
            y2: End y coordinate.
            duration: Swipe duration in seconds.

        Raises:
            RuntimeError: If ``idb`` is not installed.
        """
        self._run(
            [
                "idb",
                "ui",
                "swipe",
                "--udid",
                self.udid,
                str(x1),
                str(y1),
                str(x2),
                str(y2),
                "--duration",
                str(duration),
            ]
        )

    def type_text(self, text: str) -> None:
        """Type *text* into the currently focused field.

        Args:
            text: The string to type.

        Raises:
            RuntimeError: If ``idb`` is not installed.
        """
        self._run(["idb", "ui", "text", "--udid", self.udid, text])

    def press_key(self, key_id: int) -> None:
        """Press the hardware key identified by *key_id*.

        Args:
            key_id: Integer key code (e.g. 36 = Return/Enter).

        Raises:
            RuntimeError: If ``idb`` is not installed.
        """
        self._run(["idb", "ui", "key", "--udid", self.udid, str(key_id)])

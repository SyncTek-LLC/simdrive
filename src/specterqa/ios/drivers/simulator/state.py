"""M7: StateInspector — app state inspection for iOS Simulator.

Provides access to NSUserDefaults, keychain items, container file system,
and produces a composite state snapshot. All keychain values are redacted.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


class StateInspector:
    """Inspect the persistent state of an iOS Simulator app.

    Reads NSUserDefaults, keychain entries, and container filesystem via
    ``xcrun simctl``.  All keychain values are always redacted to
    ``"[REDACTED]"`` — sensitive data must never leave this module.

    Args:
        device_id: Simulator device UDID or "booted".
        bundle_id: The app's bundle identifier (e.g. "com.example.myapp").
    """

    def __init__(self, device_id: str, bundle_id: str) -> None:
        self.device_id = device_id
        self.bundle_id = bundle_id

    # ------------------------------------------------------------------
    # Container path
    # ------------------------------------------------------------------

    def _get_container_path(self) -> str:
        """Return the app's data container path via simctl.

        Returns:
            Stripped path string from ``xcrun simctl get_app_container``.
        """
        result = subprocess.run(
            [
                "xcrun",
                "simctl",
                "get_app_container",
                self.device_id,
                self.bundle_id,
                "data",
            ],
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    # ------------------------------------------------------------------
    # NSUserDefaults
    # ------------------------------------------------------------------

    def read_defaults(self) -> dict[str, Any]:
        """Read all NSUserDefaults for the app via simctl.

        Parses plist-style output (``key = value;`` lines) into a Python dict.
        Falls back to reading the plist file directly from the app container's
        ``Library/Preferences/<bundle_id>.plist`` when the ``defaults read``
        command returns empty output (which can happen before the app registers
        defaults with the simctl daemon).

        Returns:
            Dict of preference key→value pairs.  Values are returned as
            strings unless they look numeric (converted to int).
        """
        result = subprocess.run(
            [
                "xcrun",
                "simctl",
                "spawn",
                self.device_id,
                "defaults",
                "read",
                self.bundle_id,
            ],
            capture_output=True,
            text=True,
        )
        parsed = self._parse_plist_style(result.stdout)
        if parsed:
            return parsed

        # Fallback: read the plist file directly from the container filesystem
        return self._read_defaults_from_plist_file()

    def _read_defaults_from_plist_file(self) -> dict[str, Any]:
        """Read NSUserDefaults from the container's plist file as a fallback.

        Reads ``Library/Preferences/<bundle_id>.plist`` from the app's data
        container using ``plistlib``.  This covers the case where the simctl
        ``defaults read`` pathway fails or returns empty output.

        Returns:
            Dict of preference key→value pairs, or an empty dict if the plist
            file cannot be found or parsed.
        """
        import plistlib

        container = self._get_container_path()
        if not container:
            return {}

        plist_path = Path(container) / "Library" / "Preferences" / f"{self.bundle_id}.plist"
        if not plist_path.exists():
            return {}

        try:
            with plist_path.open("rb") as fh:
                data = plistlib.load(fh)
            if not isinstance(data, dict):
                return {}
            # Normalise values: convert numeric strings to int where possible
            result: dict[str, Any] = {}
            for key, value in data.items():
                if isinstance(value, bool):
                    result[key] = value
                elif isinstance(value, int):
                    result[key] = value
                elif isinstance(value, str):
                    try:
                        result[key] = int(value)
                    except ValueError:
                        result[key] = value
                else:
                    result[key] = value
            return result
        except Exception:
            return {}

    def read_default(self, key: str) -> Any:
        """Read a single NSUserDefaults key.

        Args:
            key: The preference key to read.

        Returns:
            The value as a string (stripped), or None if unavailable.
        """
        result = subprocess.run(
            [
                "xcrun",
                "simctl",
                "spawn",
                self.device_id,
                "defaults",
                "read",
                self.bundle_id,
                key,
            ],
            capture_output=True,
            text=True,
        )
        raw = result.stdout.strip()
        return raw if raw else None

    def write_default(self, key: str, value: Any) -> None:
        """Write a value to NSUserDefaults via simctl.

        Args:
            key: The preference key.
            value: The value to write (converted to str for the command).
        """
        subprocess.run(
            [
                "xcrun",
                "simctl",
                "spawn",
                self.device_id,
                "defaults",
                "write",
                self.bundle_id,
                key,
                str(value),
            ],
            capture_output=True,
            text=True,
        )

    # ------------------------------------------------------------------
    # Keychain
    # ------------------------------------------------------------------

    def keychain_items(self) -> list[dict]:
        """List keychain entries for the app with all values redacted.

        Parses the output of ``security find-generic-password`` for the
        bundle's service name.  Every value field is replaced with
        ``"[REDACTED]"`` — actual token or credential values are never
        returned.

        Returns:
            List of dicts, each representing one keychain entry.  The
            ``"value"`` key is always ``"[REDACTED]"`` when present.
        """
        result = subprocess.run(
            ["security", "find-generic-password", "-s", self.bundle_id, "-a", self.bundle_id],
            capture_output=True,
            text=True,
        )
        output = result.stdout
        if not output.strip():
            return []

        items: list[dict] = []
        current_item: dict[str, str] = {}

        for line in output.splitlines():
            line = line.strip()
            if line.startswith('"acct"'):
                # Extract account name
                if "=" in line:
                    acct_part = line.split("=", 1)[1].strip().strip('"')
                    current_item["account"] = acct_part
            elif line.startswith('"svce"'):
                if "=" in line:
                    svc_part = line.split("=", 1)[1].strip().strip('"')
                    current_item["service"] = svc_part
            elif line.startswith('"v_Data"') or "password" in line.lower():
                # Always redact the actual data/value
                current_item["value"] = "[REDACTED]"

            # Each entry block ends when we see another "keychain:" line
            # or we've accumulated enough fields
            if "account" in current_item and "value" not in current_item:
                current_item["value"] = "[REDACTED]"

        # Commit last item if populated
        if current_item:
            if "value" not in current_item:
                current_item["value"] = "[REDACTED]"
            items.append(current_item)

        return items

    def has_auth_token(self) -> bool:
        """Return True if the keychain contains auth-related entries.

        Checks keychain output for any entry whose account name contains
        "auth" or "token" (case-insensitive).

        Returns:
            True when at least one auth-related keychain entry exists.
        """
        result = subprocess.run(
            ["security", "find-generic-password", "-s", self.bundle_id, "-a", self.bundle_id],
            capture_output=True,
            text=True,
        )
        output = result.stdout
        if not output.strip():
            return False

        auth_keywords = ("auth", "token")
        for line in output.splitlines():
            line_lower = line.lower()
            if any(kw in line_lower for kw in auth_keywords):
                return True
        return False

    # ------------------------------------------------------------------
    # Container filesystem
    # ------------------------------------------------------------------

    def list_documents(self) -> list[str]:
        """List files in the app's Documents directory.

        Returns:
            List of filename strings (leaf names only) found in Documents/.
        """
        container = self._get_container_path()
        documents_dir = Path(container) / "Documents"
        if not documents_dir.exists():
            return []
        return [entry.name for entry in documents_dir.iterdir() if entry.is_file()]

    def file_exists(self, relative_path: str) -> bool:
        """Check whether a file exists inside the app container.

        Args:
            relative_path: Path relative to the container root
                (e.g. ``"Documents/session.json"``).

        Returns:
            True if the file exists; False otherwise.
        """
        container = self._get_container_path()
        full_path = Path(container) / relative_path
        return full_path.exists()

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------

    def snapshot(self) -> dict:
        """Return a composite dict of all app state.

        Keys:
            - ``user_defaults``: dict from :meth:`read_defaults`
            - ``has_auth_token``: bool from :meth:`has_auth_token`
            - ``document_count``: int — number of files in Documents/
            - ``container_path``: str from :meth:`_get_container_path`

        Returns:
            Dict aggregating current app state.
        """
        container_path = self._get_container_path()
        user_defaults = self.read_defaults()
        auth_token = self.has_auth_token()
        documents = self.list_documents()

        return {
            "user_defaults": user_defaults,
            "has_auth_token": auth_token,
            "document_count": len(documents),
            "container_path": container_path,
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_plist_style(text: str) -> dict[str, Any]:
        """Parse plist-style ``key = value;`` output from ``defaults read``.

        Handles the format produced by the macOS ``defaults`` command:
        ``{\\n    Key = Value;\\n    ...\\n}``

        Args:
            text: Raw stdout from the defaults command.

        Returns:
            Dict with parsed key/value pairs.  Values are converted to int
            when possible; otherwise left as str.
        """
        result: dict[str, Any] = {}
        for line in text.splitlines():
            line = line.strip()
            # Skip braces and blank lines
            if not line or line in ("{", "}"):
                continue
            # Strip trailing semicolon
            if line.endswith(";"):
                line = line[:-1].rstrip()
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"')
            # Attempt numeric coercion
            try:
                result[key] = int(value)
            except ValueError:
                result[key] = value
        return result

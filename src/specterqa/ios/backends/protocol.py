"""IOSBackend Protocol — structural contract for all SpecterQA iOS backends.

Every backend that participates in the ``BackendSelector.choose()`` flow must
satisfy this Protocol.  Because it is marked ``@runtime_checkable``, callers can
use ``isinstance(obj, IOSBackend)`` as a sanity check, though the primary value
is static-type-checked duck typing.

[internal-tracker] — SpecterQA iOS Protocol refactor.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


class NotSupportedError(NotImplementedError):
    """Raised by a backend method that is structurally unsupported.

    Example: ``AXBackend.source()`` cannot produce an XCTest-style XML tree;
    it should raise ``NotSupportedError`` rather than returning partial data.
    """


@runtime_checkable
class IOSBackend(Protocol):
    """Structural type shared by all SpecterQA iOS interaction backends.

    Backends do not need to inherit from this class — they only need to
    implement the methods below with compatible signatures.

    Method summary
    --------------
    Probe:
        is_available        Cheap class-level probe; no side-effects.

    Lifecycle:
        start               Launch / connect to a session on the device.
        stop                Tear down the session and release resources.

    Status:
        health              Lightweight ping → ``{"status": "ok"|"unreachable", "pid": int|None}``
        app_state           App lifecycle string (foreground/background/suspended/not_running)

    Interaction:
        tap                 Tap by coordinate and/or element descriptor.
        swipe               Swipe in a cardinal direction.
        type_text           Type text, optionally targeting an element.
        press_key           Press a named key.

    Observation:
        get_elements        Full or truncated element tree.
        find_element        Search for a single element by criteria.
        screenshot          Capture screenshot bytes (PNG).
        source              XML/plist accessibility source; raises NotSupportedError if unavailable.
    """

    # ------------------------------------------------------------------
    # Class-level availability probe
    # ------------------------------------------------------------------

    @classmethod
    def is_available(cls) -> bool:
        """Return True when this backend can be used on the current machine.

        This is a cheap, side-effect-free probe.  It must not start any
        process or take more than ~1 second to complete.

        Returns:
            ``True`` if the backend is usable, ``False`` otherwise.
        """
        ...

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self, device_udid: str, bundle_id: str, **kwargs: object) -> None:
        """Start a session for *bundle_id* on *device_udid*.

        This method is idempotent: calling it when a session is already
        running is a no-op or raises ``RuntimeError`` — backend-dependent.

        Args:
            device_udid: Simulator UDID or ``"booted"``.
            bundle_id:   App bundle identifier (e.g. ``"com.example.MyApp"``).
            **kwargs:    Backend-specific options (e.g. ``app_path``, ``clone``).
        """
        ...

    def stop(self) -> None:
        """Stop the session and release all resources.

        Should be safe to call more than once (subsequent calls are no-ops).
        """
        ...

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def health(self) -> dict:
        """Return a lightweight health dict.

        Returns:
            ``{"status": "ok"|"unreachable", "pid": int|None}``
        """
        ...

    def app_state(self) -> str:
        """Return the app lifecycle state.

        Returns:
            One of: ``"foreground"``, ``"background"``, ``"suspended"``,
            ``"not_running"``, ``"unknown"``.
        """
        ...

    # ------------------------------------------------------------------
    # Interaction
    # ------------------------------------------------------------------

    def tap(
        self,
        x: float | None = None,
        y: float | None = None,
        label: str | None = None,
        identifier: str | None = None,
        element_index: int | None = None,
    ) -> dict:
        """Tap by coordinate or element descriptor.

        At least one of *x*/*y* (coordinate pair) or *label*/*identifier*/
        *element_index* (element descriptor) must be provided.

        Returns:
            Response dict (at minimum ``{"success": bool}``).
        """
        ...

    def swipe(self, direction: str, duration_s: float = 0.3) -> dict:
        """Swipe in a cardinal direction.

        Args:
            direction:  ``"up"``, ``"down"``, ``"left"``, or ``"right"``.
            duration_s: Gesture duration in seconds.

        Returns:
            Response dict.
        """
        ...

    def type_text(
        self,
        text: str,
        label: str | None = None,
        identifier: str | None = None,
        element_index: int | None = None,
    ) -> dict:
        """Type *text* into the focused field, optionally targeting an element first.

        Returns:
            Response dict.
        """
        ...

    def press_key(self, key: str) -> dict:
        """Press a named key (e.g. ``"return"``, ``"escape"``, ``"delete"``).

        Returns:
            Response dict.
        """
        ...

    # ------------------------------------------------------------------
    # Observation
    # ------------------------------------------------------------------

    def get_elements(self, max_elements: int = 0) -> dict:
        """Return the element tree.

        Args:
            max_elements: Maximum number of elements to return.  ``0`` means
                          no limit (up to the backend's own cap).

        Returns:
            Dict with at least ``{"elements": list, "count": int}``.
        """
        ...

    def find_element(self, **criteria: object) -> dict | None:
        """Find a single element matching *criteria*.

        Common criteria keys: ``label``, ``identifier``, ``element_type``,
        ``value``, ``index``.

        Returns:
            Element dict if found, ``None`` otherwise.
        """
        ...

    def screenshot(self, quality: str = "standard") -> bytes:
        """Capture a screenshot and return raw PNG bytes.

        Args:
            quality: ``"standard"`` (default) or ``"high"`` — backend-dependent.

        Returns:
            Raw PNG bytes.
        """
        ...

    def source(self) -> dict:
        """Return the XML/plist accessibility source.

        Returns:
            Dict with at least ``{"xml": str}`` on success.

        Raises:
            NotSupportedError: When the backend cannot produce an XML source.
        """
        ...

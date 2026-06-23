"""Set-of-Mark annotation: detect text regions, number them, draw boxes.

Internal module. Uses macOS Vision framework via pyobjc for OCR — already
available since we depend on pyobjc-framework-Quartz. No extra ML deps,
no remote calls.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# v0.3.0a3 — small inline dictionary used to dictionary-gate raw OCR confidence.
# Stylized cover art frequently OCRs as plausible-looking gibberish ("Sary of the
# Canadan liothest") at confidence 1.0; vowel-ratio alone doesn't catch it because
# the misreads still contain real words mixed with fake ones. The dictionary check
# is the fence: a mark whose tokens mostly aren't in the wordlist (and aren't short
# or numeric) gets clamped down even when raw_confidence is 1.0.
#
# Keep this list <= 200 entries: stop words + common UI vocabulary. Real-world
# usage will tell us what to add when something legitimate gets clamped.
_ENGLISH_WORDS: frozenset[str] = frozenset(
    {
        # function words / pronouns / common verbs
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
        "am", "have", "has", "had", "having", "do", "did", "does", "doing",
        "of", "in", "on", "at", "to", "from", "by", "for", "with", "without",
        "about", "as", "and", "or", "but", "not", "no", "yes", "if", "then",
        "than", "so", "this", "that", "these", "those", "it", "its", "i",
        "you", "your", "we", "our", "us", "they", "them", "their", "he",
        "she", "his", "her", "him", "me", "my", "mine", "yours", "ours",
        # UI verbs
        "ok", "cancel", "done", "next", "back", "previous", "close", "save",
        "delete", "edit", "add", "remove", "new", "open", "select", "submit",
        "send", "receive", "share", "send", "view", "show", "hide", "more",
        "less", "search", "filter", "sort", "copy", "cut", "paste", "undo",
        "redo", "refresh", "reload", "retry", "play", "pause", "stop", "start",
        "resume", "skip", "follow", "unfollow", "like", "comment", "post",
        # auth / account
        "sign", "signin", "signup", "login", "logout", "register", "email",
        "password", "username", "name", "first", "last", "phone", "address",
        "settings", "profile", "account", "help", "about", "support",
        # status
        "loading", "please", "try", "again", "error", "success", "failed",
        "warning", "alert", "info", "ready", "complete", "completed",
        "pending", "active", "inactive",
        # nav
        "home", "tab", "menu", "list", "grid", "page", "pages", "all", "any",
        "some", "none", "every",
        # reader / library domain
        "library", "book", "books", "read", "reading", "title", "author",
        "content", "chapter", "return", "borrow", "hold", "catalog", "cart",
        "ebook", "audiobook", "audio", "video", "media", "magazine",
        "library", "shelf", "shelves", "favorites", "downloaded",
        # generic content
        "title", "subtitle", "description", "summary", "details", "details",
        # directions / states
        "up", "down", "left", "right", "top", "bottom", "center",
        "on", "off", "true", "false", "now", "today", "yesterday",
        "tomorrow", "date", "time",
        # quantities
        "one", "two", "three", "four", "five", "six", "seven", "eight",
        "nine", "ten", "many", "few",
        # connectives
        "because", "since", "while", "when", "where", "what", "who", "how",
        "why", "which", "into", "onto", "out", "over", "under",
        # confirmations
        "confirm", "agree", "accept", "decline", "allow", "deny", "skip",
        # date / calendar
        "monday", "tuesday", "wednesday", "thursday", "friday", "saturday",
        "sunday", "january", "february", "march", "april", "may", "june",
        "july", "august", "september", "october", "november", "december",
        # misc UI
        "welcome", "hello", "goodbye", "logout", "trial", "free", "premium",
        "upgrade", "subscribe", "subscription",
        # iOS settings / system UI vocabulary (F#18 — Apple Preferences labels)
        "general", "privacy", "bluetooth", "wi-fi", "wifi", "notifications",
        "sounds", "haptics", "focus", "screen", "time", "accessibility",
        "siri", "safari", "maps", "health", "wallet", "facetime", "photos",
        "camera", "messages", "mail", "calendar", "contacts", "reminders",
        "notes", "icloud", "itunes", "store", "appstore", "airdrop", "airplay",
        "display", "brightness", "battery", "storage", "privacy", "security",
        "passcode", "touchid", "faceid", "cellular", "vpn", "hotspot",
        "language", "region", "keyboard", "reset", "update", "software",
        # common content nouns / verbs that show up in titles & cells
        "dance", "partner", "story", "tale", "world", "people", "person",
        "place", "thing", "year", "day", "way", "man", "woman", "child",
        "life", "hand", "part", "case", "week", "company", "system",
        "program", "question", "work", "government", "number", "night",
        "point", "house", "money", "fact", "month", "lot", "right", "study",
        "job", "word", "issue", "side", "kind", "head", "father", "mother",
        "force", "moment", "air", "war", "history", "party", "result",
        "change", "morning", "reason", "research", "girl", "boy", "guy",
        "moment", "music", "film", "movie", "show",
    }
)


# v0.3.0a3 — semantic-name → likely-OCR-misread lookup for icon glyphs.
# OCR rasterizes the search magnifying glass as "Q/" / "Q." / "O" depending
# on resolution. Stable IDs catch it for replay against the same screen, but
# `text="search"` from a fresh agent doesn't match. This whitelist lets
# find_by_text fall back to alias lookup when no direct match is found.
#
# Canonical form: `{semantic_name: [list of OCR strings the glyph commonly
# rasterizes as, including the semantic name itself]}`. Match is
# case-insensitive on the mark text against the alias list. Keep this seed
# set small — real-world usage will tell us what's missing.
_ICON_GLYPH_ALIASES: dict[str, list[str]] = {
    # search magnifying glass renders as Q, Q/, Q., Q\, O, etc.
    "search": ["q", "q/", "q.", "q\\", "o", "search"],
    # back / chevron-left
    "back": ["<", "‹", "back"],
    # forward / chevron-right
    "forward": [">", "›", "forward", "next"],
    # gear / settings
    "settings": ["⚙", "settings", "gear"],
    # hamburger / menu
    "menu": ["≡", "menu", "☰"],
    # close / x
    "close": ["x", "✕", "✖", "close", "cancel"],
    # plus / add
    "add": ["+", "add", "new"],
}


_ALLOWED_PUNCT = set("-'.,!?:")
_VOWELS = set("aeiouAEIOU")
_TOKEN_RE = re.compile(r"\s+")


def _is_english_like_token(token: str) -> bool:
    """A token passes if: in dictionary, OR <= 3 chars, OR fully numeric."""
    if not token:
        return True  # ignore empty splits
    if token.isdigit():
        return True
    cleaned = token.strip("".join(_ALLOWED_PUNCT)).lower()
    if not cleaned:
        return True  # punctuation-only fragment
    if len(cleaned) <= 3:
        return True
    return cleaned in _ENGLISH_WORDS


def _english_likeness(text: str) -> bool:
    """Return True if `text` looks like real English (dictionary-gated).

    Cheap pre-checks first (charset, token length, vowel ratio); then the
    dictionary check decides borderline cases. Stylized OCR misreads typically
    contain real words AND fake ones at high vowel ratios — only the dictionary
    fence catches those reliably.
    """
    s = (text or "").strip()
    if not s:
        return False
    # Charset: letters/digits/spaces/basic punctuation only
    for ch in s:
        if ch.isalnum() or ch.isspace() or ch in _ALLOWED_PUNCT:
            continue
        return False
    tokens = [t for t in _TOKEN_RE.split(s) if t]
    if not tokens:
        return False
    # Length sanity: real words don't exceed ~25 chars
    if any(len(t) > 25 for t in tokens):
        return False
    # Vowel ratio sanity: most tokens contain at least one vowel
    voweled = sum(1 for t in tokens if any(c in _VOWELS for c in t))
    if tokens and voweled / len(tokens) < 0.6:
        # Acronym escape: any all-caps token of length >= 4 is OK
        if not any(t.isupper() and len(t) >= 4 for t in tokens):
            return False
    # Dictionary fence: >= 50% of tokens must be english-like
    eligible = [t for t in tokens if not (t.isdigit() or len(t) <= 3)]
    if not eligible:
        # Everything is short/numeric — let it pass (single-letter labels, "OK", "12")
        return True
    likes = sum(1 for t in tokens if _is_english_like_token(t))
    return (likes / len(tokens)) >= 0.5


@dataclass
class Mark:
    id: int
    x: int
    y: int
    w: int
    h: int
    text: str
    confidence: float  # legacy; in v0.3.0a3 this is the *gated* score

    # v0.3.0a3 — `raw_confidence` preserves the OCR engine's unclamped value.
    # When None, callers reading `raw_confidence` see the unclamped legacy value
    # (set in __post_init__ from `confidence`).
    raw_confidence: Optional[float] = None
    # `confidence_band` is the dictionary-gated quality bucket. None = compute lazily.
    _band: Optional[str] = field(default=None, repr=False)
    # F#4 — b5: alternate OCR readings for this element seen across consecutive
    # observations. Populated by the OCR smoothing layer when consecutive observes
    # produce different text for the same spatial region. Defaults to empty list;
    # callers may set this after construction.
    alternates: list = field(default_factory=list)

    def __post_init__(self) -> None:
        # If callers constructed a Mark with only `confidence`, that value is
        # the raw OCR score — preserve it as `raw_confidence`, then compute the
        # band and clamp `confidence` accordingly.
        if self.raw_confidence is None:
            self.raw_confidence = float(self.confidence)
        # Compute band once.
        self._band = self._compute_band()
        # Clamp legacy confidence per the band.
        self.confidence = self._clamped_confidence()

    def _compute_band(self) -> str:
        """Three-band quality bucket — gated primarily on the dictionary.

        Misreads at raw 1.0 (stylized covers OCRing as plausible-looking
        gibberish) fail the English-likeness check and surface as "low" even
        with a perfect engine-side score. That's the dogfood signal real-app
        engineers can trust.
        """
        raw = float(self.raw_confidence or 0.0)
        english_like = _english_likeness(self.text)
        if not english_like:
            # Dictionary fence failed — the OCR doesn't read as English. Don't
            # promote on raw confidence alone; this is the case the v0.3.0a3
            # gating exists to catch.
            return "low"
        if raw >= 0.85:
            return "high"
        return "medium"

    def _clamped_confidence(self) -> float:
        raw = float(self.raw_confidence or 0.0)
        if self._band == "high":
            return raw
        if self._band == "medium":
            return min(raw, 0.5)
        return min(raw, 0.3)

    @property
    def confidence_band(self) -> str:
        return self._band or self._compute_band()

    @property
    def center(self) -> tuple[int, int]:
        return (self.x + self.w // 2, self.y + self.h // 2)

    @property
    def stable_id(self) -> str:
        """A short hash of (text, ~position) that survives observe() reshuffling.

        IDs from `id` change every observe (top-to-bottom ordering); stable_id
        stays the same as long as the same element keeps the same text and
        appears in roughly the same place (rounded to 20px buckets).
        """
        bucket_x = (self.x + self.w // 2) // 20
        bucket_y = (self.y + self.h // 2) // 20
        key = f"{self.text}|{bucket_x},{bucket_y}".encode("utf-8")
        return hashlib.blake2b(key, digest_size=6).hexdigest()

    @property
    def stable_id_loose(self) -> str:
        """Coarser companion to stable_id — 60px bucket (3x tight) tolerates layout drift."""
        bucket_x = (self.x + self.w // 2) // 60
        bucket_y = (self.y + self.h // 2) // 60
        key = f"{self.text}|{bucket_x},{bucket_y}".encode("utf-8")
        return hashlib.blake2b(key, digest_size=6).hexdigest()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "stable_id": self.stable_id,
            "stable_id_loose": self.stable_id_loose,
            "bbox": [self.x, self.y, self.w, self.h],
            "center": list(self.center),
            "text": self.text,
            # `confidence` stays legacy: dictionary-clamped so a misread at
            # raw 1.0 with no dictionary hits surfaces as <= 0.3. Existing
            # callers that filter on `confidence > 0.7` get the dogfood fix
            # for free.
            "confidence": round(self.confidence, 3),
            # `raw_confidence` exposes the unclamped OCR engine score; engineers
            # comparing against Vision's own threshold get an honest number.
            "raw_confidence": round(float(self.raw_confidence or 0.0), 3),
            # `confidence_band` is the human-readable quality bucket.
            "confidence_band": self.confidence_band,
            # F#4 — alternate OCR readings seen across consecutive observations.
            "alternates": list(self.alternates),
        }

    def to_compact_dict(self) -> dict:
        """Slim mark dict for token-efficient `observe(compact=True)` responses.

        Drops OCR diagnostic fields (`raw_confidence`, `confidence`,
        `stable_id_loose`) that most agents never read. Retains the six keys
        agents typically need to act on a mark: identifier, stable identifier,
        text, geometry, and quality bucket.

        Token cost per mark drops from ~20 keys (to_dict) to 6 — roughly
        5-6x reduction in JSON payload size on dense screens.
        """
        return {
            "id": self.id,
            "stable_id": self.stable_id,
            "text": self.text,
            "center": list(self.center),
            "bbox": [self.x, self.y, self.w, self.h],
            "confidence_band": self.confidence_band,
        }


def vision_available() -> bool:
    try:
        import Vision  # noqa: F401
        return True
    except Exception:
        return False


def detect_marks(image_path: Path) -> list[Mark]:
    """Run macOS Vision OCR; return numbered marks ordered top-to-bottom, left-to-right."""
    if not vision_available():
        return []
    try:
        from Foundation import NSURL
        from Vision import (
            VNImageRequestHandler,
            VNRecognizeTextRequest,
            VNRequestTextRecognitionLevelAccurate,
        )
        from PIL import Image

        url = NSURL.fileURLWithPath_(str(image_path))
        handler = VNImageRequestHandler.alloc().initWithURL_options_(url, None)
        request = VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(VNRequestTextRecognitionLevelAccurate)
        request.setUsesLanguageCorrection_(False)

        success, _err = handler.performRequests_error_([request], None)
        if not success:
            return []
        with Image.open(image_path) as im:
            img_w, img_h = im.size

        raw: list[tuple[int, int, int, int, str, float]] = []
        for obs in request.results() or []:
            bbox = obs.boundingBox()
            cands = obs.topCandidates_(1)
            if cands is None or len(cands) == 0:
                continue
            cand = cands[0]
            text = str(cand.string())
            conf = float(cand.confidence())

            # Vision: normalized 0-1, origin BOTTOM-LEFT
            ox, oy = float(bbox.origin.x), float(bbox.origin.y)
            ow, oh = float(bbox.size.width), float(bbox.size.height)
            x = int(ox * img_w)
            w = int(ow * img_w)
            h = int(oh * img_h)
            y = int((1.0 - oy - oh) * img_h)
            raw.append((x, y, w, h, text, conf))

        # Sort top-to-bottom, then left-to-right (rough reading order)
        raw.sort(key=lambda r: (r[1] // 40, r[0]))
        return [
            Mark(id=i + 1, x=x, y=y, w=w, h=h, text=text, confidence=conf)
            for i, (x, y, w, h, text, conf) in enumerate(raw)
        ]
    except Exception:
        return []


def annotate(image_path: Path, marks: list[Mark], out_path: Path) -> Path:
    """Draw numbered red boxes + label badges on the screenshot."""
    from PIL import Image, ImageDraw, ImageFont

    im = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(im)

    # Pick a font size that scales with image size
    font_size = max(18, im.height // 80)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", font_size)
    except Exception:
        font = ImageFont.load_default()

    for m in marks:
        # Box outline
        draw.rectangle([m.x, m.y, m.x + m.w, m.y + m.h], outline=(255, 0, 0), width=3)
        # Numbered badge above box
        label = str(m.id)
        try:
            tw = int(draw.textlength(label, font=font))
        except Exception:
            tw = font_size * len(label)
        th = font_size + 8
        bx0 = m.x
        by0 = max(0, m.y - th)
        bx1 = bx0 + tw + 12
        by1 = by0 + th
        draw.rectangle([bx0, by0, bx1, by1], fill=(255, 0, 0))
        draw.text((bx0 + 6, by0 + 2), label, fill=(255, 255, 255), font=font)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    im.save(out_path)
    return out_path


def _mark_get(m: "Mark | dict", key: str):
    """Uniform attribute/key access for a mark that may be a Mark dataclass or a dict.

    a12 — marks are normalised to ``dict`` end-to-end (sim and device paths both
    store ``list[dict]`` in ``Session.last_marks``).  This helper retains backwards
    compatibility for any callers that still pass ``Mark`` dataclass instances.
    """
    if isinstance(m, dict):
        return m.get(key)
    return getattr(m, key, None)


def find_by_text(marks: "list[Mark | dict]", query: str) -> "Optional[Mark | dict]":
    """Best mark matching `query`. Exact > prefix > substring (case-insensitive).

    v0.3.0a3 — when no direct text match is found, fall back to the icon-glyph
    alias whitelist: `find_by_text(marks, "search")` resolves to the
    magnifying-glass mark whose OCR text is "Q/" / "Q." / "O" / etc. Aliases
    are a *final* fallback; exact/prefix/substring still win when present, so
    behavior is backwards-compatible for any caller that wasn't relying on
    glyph fallbacks.

    a12 — accepts both ``Mark`` dataclass instances and plain ``dict`` marks so
    that sim and device paths share a single code path.
    """
    q = query.strip().lower()
    if not q or not marks:
        return None
    exact = [m for m in marks if (_mark_get(m, "text") or "").strip().lower() == q]
    if exact:
        return max(exact, key=lambda m: _mark_get(m, "confidence") or 0)
    prefix = [m for m in marks if (_mark_get(m, "text") or "").strip().lower().startswith(q)]
    if prefix:
        return max(prefix, key=lambda m: _mark_get(m, "confidence") or 0)
    sub = [m for m in marks if q in (_mark_get(m, "text") or "").lower()]
    if sub:
        return max(sub, key=lambda m: _mark_get(m, "confidence") or 0)
    # Final fallback: icon-glyph semantic-name aliases.
    aliases = _ICON_GLYPH_ALIASES.get(q)
    if aliases:
        alias_set = {a.lower() for a in aliases}
        cands = [m for m in marks if (_mark_get(m, "text") or "").strip().lower() in alias_set]
        if cands:
            return max(cands, key=lambda m: _mark_get(m, "confidence") or 0)
    return None


def find_text_candidates(
    marks: "list[Mark | dict]", query: str
) -> "tuple[list[Mark | dict], str]":
    """Return every mark tied at the best-matching precedence tier + tier name.

    F#6 — ``find_by_text`` silently picked the first match when duplicate labels
    existed (e.g. SimDrive Demo screen title and submit button both reading
    "Sign In"). This helper exposes the full candidate set at the *winning* tier
    so callers can raise an ``ambiguous_text_target`` error when more than one
    mark ties at that tier.

    Tier precedence: ``exact`` > ``prefix`` > ``substring`` > ``alias``. Only
    the highest non-empty tier is returned — if there is 1 exact match and 5
    prefix matches, only the exact match is returned (single candidate, no
    ambiguity). If 2 exact matches exist, only those 2 are returned, and any
    prefix/substring matches fall through.

    Returns ``([], "")`` when no marks match at any tier.
    """
    q = query.strip().lower()
    if not q or not marks:
        return [], ""

    exact = [m for m in marks if (_mark_get(m, "text") or "").strip().lower() == q]
    if exact:
        return exact, "exact"
    prefix = [m for m in marks if (_mark_get(m, "text") or "").strip().lower().startswith(q)]
    if prefix:
        return prefix, "prefix"
    sub = [m for m in marks if q in (_mark_get(m, "text") or "").lower()]
    if sub:
        return sub, "substring"
    aliases = _ICON_GLYPH_ALIASES.get(q)
    if aliases:
        alias_set = {a.lower() for a in aliases}
        alias_cands = [
            m for m in marks if (_mark_get(m, "text") or "").strip().lower() in alias_set
        ]
        if alias_cands:
            return alias_cands, "alias"
    return [], ""


def find_by_mark_id(marks: "list[Mark | dict]", mark_id: int) -> "Optional[Mark | dict]":
    """a12 — accepts both Mark dataclasses and dict marks."""
    for m in marks:
        if _mark_get(m, "id") == mark_id:
            return m
    return None


def find_by_stable_id(marks: "list[Mark | dict]", stable_id: str) -> "Optional[Mark | dict]":
    """a12 — accepts both Mark dataclasses and dict marks."""
    for m in marks:
        if _mark_get(m, "stable_id") == stable_id:
            return m
    return None


def find_by_stable_id_loose(marks: "list[Mark | dict]", stable_id: str) -> "Optional[Mark | dict]":
    """a12 — accepts both Mark dataclasses and dict marks."""
    for m in marks:
        if _mark_get(m, "stable_id_loose") == stable_id:
            return m
    return None

"""Integration tests for Element Resolver v2 — scored matching.

Extracted from test_dogfood_fixes.py (the 6 TestLookupScoredMatching tests).

These use real dataclass MockElement objects, NOT MagicMock. They test the
scoring contract of _lookup() from the real server module.

Run:
    pytest tests/integration/test_element_resolver.py -v --tb=short
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest


# ---------------------------------------------------------------------------
# Shared real element dataclass (matches spec — NOT a MagicMock)
# ---------------------------------------------------------------------------


@dataclass
class MockElement:
    index: int = 0
    label: str = ""
    identifier: str = ""
    element_type: str = "Button"
    x: float = 100.0
    y: float = 200.0
    width: float = 50.0
    height: float = 30.0
    hittable: bool = True


# ---------------------------------------------------------------------------
# TestLookupScoredMatching — 6 tests, no mocks
# ---------------------------------------------------------------------------


class TestLookupScoredMatching:
    """Verify _lookup() scores elements correctly and returns the best match."""

    def _get_lookup(self):
        """Try to import _lookup from server; skip if not yet implemented."""
        try:
            from specterqa.ios.mcp.server import _lookup
            return _lookup
        except ImportError:
            pytest.skip("_lookup not yet implemented")

    def test_lookup_exact_match_beats_substring(self):
        """'Password' exact-matches SecureTextField 'Password', NOT 'Forgot your password?'."""
        _lookup = self._get_lookup()

        password_field = MockElement(
            index=1, label="Password", identifier="", element_type="SecureTextField"
        )
        forgot_btn = MockElement(
            index=2, label="Forgot your password?", identifier="", element_type="Button"
        )
        elements = [forgot_btn, password_field]

        result = _lookup(label="Password", identifier=None, element_index=None, element_type=None, elements=elements)
        assert result is password_field, (
            f"Exact match 'Password' should win over substring match. Got: {result}"
        )

    def test_lookup_prefix_match_beats_substring(self):
        """'Pass' prefix of 'Password' beats substring of 'Forgot your password?'."""
        _lookup = self._get_lookup()

        password_field = MockElement(
            index=1, label="Password", identifier="", element_type="SecureTextField"
        )
        forgot_btn = MockElement(
            index=2, label="Forgot your password?", identifier="", element_type="Button"
        )
        elements = [forgot_btn, password_field]

        result = _lookup(label="Pass", identifier=None, element_index=None, element_type=None, elements=elements)
        assert result is password_field, (
            "'Pass' prefix of 'Password' should beat substring of 'Forgot your password?'"
        )

    def test_lookup_shorter_label_wins_on_same_score(self):
        """Two exact matches — shorter label wins (less ambiguous)."""
        _lookup = self._get_lookup()

        short_el = MockElement(index=1, label="OK", identifier="", element_type="Button")
        long_el = MockElement(index=2, label="OK Button", identifier="", element_type="Button")
        elements = [long_el, short_el]

        result = _lookup(label="OK", identifier=None, element_index=None, element_type=None, elements=elements)
        assert result is short_el, "Shorter exact match should win over longer one"

    def test_lookup_type_filter_narrows(self):
        """label='Password' type='SecureTextField' picks the correct element."""
        _lookup = self._get_lookup()

        label_el = MockElement(
            index=1, label="Password", identifier="", element_type="StaticText"
        )
        secure_field = MockElement(
            index=2, label="Password", identifier="", element_type="SecureTextField"
        )
        elements = [label_el, secure_field]

        result = _lookup(label="Password", identifier=None, element_index=None, element_type="SecureTextField", elements=elements)
        assert result is secure_field, "Type filter should narrow to SecureTextField"

    def test_lookup_identifier_takes_priority(self):
        """Identifier match returns even if label also matches a different element."""
        _lookup = self._get_lookup()

        label_match = MockElement(
            index=1, label="Save", identifier="wrongId", element_type="Button"
        )
        id_match = MockElement(
            index=2, label="SomethingElse", identifier="saveBtn", element_type="Button"
        )
        elements = [label_match, id_match]

        result = _lookup(label="Save", identifier="saveBtn", element_index=None, element_type=None, elements=elements)
        assert result is id_match, "Identifier match must take priority over label match"

    def test_lookup_index_no_scoring(self):
        """index=5 returns element with index 5, no scoring applied."""
        _lookup = self._get_lookup()

        el3 = MockElement(index=3, label="Three", identifier="", element_type="Button")
        el5 = MockElement(index=5, label="Five", identifier="", element_type="Button")
        el7 = MockElement(index=7, label="Seven", identifier="", element_type="Button")
        elements = [el3, el5, el7]

        result = _lookup(label=None, identifier=None, element_index=5, element_type=None, elements=elements)
        assert result is el5, "Index lookup must return element with matching index"

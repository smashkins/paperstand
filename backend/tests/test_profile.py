"""Profile loading: ``extends`` resolution and validation at load time."""

from __future__ import annotations

from typing import Any

import pytest

from paperstand.parsing.profile import (
    DEFAULT_PROFILE,
    ProfileError,
    build_registry,
    load_profile,
    load_profiles,
    merge_profiles,
)


def load(parsers: dict[str, dict[str, Any]], name: str) -> Any:
    return load_profile(name, build_registry(parsers))


def test_the_default_profile_is_always_available() -> None:
    profiles = load_profiles()
    assert DEFAULT_PROFILE in profiles
    default = profiles[DEFAULT_PROFILE]
    assert default.languages == ["it", "en"]
    assert default.number is True
    assert default.unsorted is True
    assert default.title_source == ["config", "pattern", "folder", "filename"]
    assert default.date_fallback == ["folder", "mtime"]


def test_a_profile_without_extends_starts_empty() -> None:
    profile = load({"bare": {}}, "bare")
    assert profile.strip == []
    assert profile.languages == []
    assert profile.patterns == []


def test_lists_replace_by_default() -> None:
    profile = load({"child": {"extends": "default", "strip": [r"^x_"]}}, "child")
    assert profile.strip == [r"^x_"]


def test_a_leading_plus_appends() -> None:
    default = load_profiles()[DEFAULT_PROFILE]
    profile = load({"child": {"extends": "default", "strip": ["+", r"^x_"]}}, "child")
    assert profile.strip == [*default.strip, r"^x_"]


def test_scalars_replace() -> None:
    profile = load({"child": {"extends": "default", "number": False}}, "child")
    assert profile.number is False
    assert profile.languages == ["it", "en"]


def test_extends_chains() -> None:
    parsers: dict[str, dict[str, Any]] = {
        "middle": {"extends": "default", "languages": ["en"]},
        "leaf": {"extends": "middle", "unsorted": False},
    }
    profile = load(parsers, "leaf")
    assert profile.languages == ["en"]
    assert profile.unsorted is False


def test_merge_is_shallow_and_drops_extends() -> None:
    merged = merge_profiles({"number": True, "strip": ["a"]}, {"extends": "x", "strip": ["+", "b"]})
    assert merged == {"number": True, "strip": ["a", "b"]}


def test_an_unknown_extends_target_names_the_profile() -> None:
    with pytest.raises(ProfileError, match="profile 'ghost': no such profile"):
        load({"child": {"extends": "ghost"}}, "child")


def test_a_cycle_is_reported() -> None:
    parsers = {"a": {"extends": "b"}, "b": {"extends": "a"}}
    with pytest.raises(ProfileError, match="cycle"):
        load(parsers, "a")


def test_an_invalid_regex_names_the_profile_and_the_pattern() -> None:
    with pytest.raises(ProfileError) as error:
        load({"broken": {"patterns": ["^(?P<title>"]}}, "broken")
    message = str(error.value)
    assert "profile 'broken'" in message
    assert "^(?P<title>" in message


def test_an_unknown_named_group_names_the_profile_and_the_pattern() -> None:
    pattern = r"^(?P<publication>.+?)_(?P<year>\d{4})$"
    with pytest.raises(ProfileError) as error:
        load({"broken": {"patterns": [pattern]}}, "broken")
    message = str(error.value)
    assert "profile 'broken'" in message
    assert pattern in message
    assert "publication" in message


def test_an_unknown_language_is_refused() -> None:
    with pytest.raises(ProfileError) as error:
        load({"broken": {"languages": ["klingon"]}}, "broken")
    assert "klingon" in str(error.value)


def test_a_custom_month_table_makes_a_language_known() -> None:
    profile = load({"ok": {"languages": ["xx"], "months": {"xx": {"primo": 1}}}}, "ok")
    assert profile.languages == ["xx"]


def test_an_unknown_date_rule_is_refused() -> None:
    with pytest.raises(ProfileError, match="D9"):
        load({"broken": {"date_rules": ["D9"]}}, "broken")


def test_an_unknown_key_is_refused() -> None:
    with pytest.raises(ProfileError, match="typo"):
        load({"broken": {"typo": True}}, "broken")


def test_a_user_profile_may_replace_the_bundled_one() -> None:
    profiles = load_profiles({"default": {"languages": ["en"]}})
    assert profiles["default"].languages == ["en"]

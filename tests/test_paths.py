"""Output path safety."""

from __future__ import annotations

from pathlib import Path

from wayback_image_recovery.paths import flat_relative_path, is_inside, safe_relative_path


def test_path_keeps_host_and_suffix() -> None:
    relative = safe_relative_path("https://CDN.Example.com:8080/images/logo.png")
    assert relative.as_posix() == "cdn.example.com_8080/images/logo.png"


def test_dotdot_and_encoded_traversal_stay_inside() -> None:
    relative = safe_relative_path("https://example.com/%2e%2e/%2e%2e/etc/passwd.png")
    assert ".." not in relative.parts
    assert relative.as_posix() == "example.com/etc/passwd.png"


def test_query_strings_do_not_collapse() -> None:
    left = safe_relative_path("https://example.com/img.png?id=1")
    right = safe_relative_path("https://example.com/img.png?id=2")
    assert left != right
    assert left.suffix == ".png"


def test_flat_name_uses_hash_prefix() -> None:
    relative = flat_relative_path("https://example.com/a.jpeg", "a" * 64)
    assert relative.as_posix() == ("a" * 16) + ".jpeg"


def test_is_inside_rejects_escape() -> None:
    root = Path("/tmp/recovered-root")
    assert is_inside(root, root / "example.com" / "a.png")
    assert not is_inside(root, root / ".." / "etc" / "passwd")

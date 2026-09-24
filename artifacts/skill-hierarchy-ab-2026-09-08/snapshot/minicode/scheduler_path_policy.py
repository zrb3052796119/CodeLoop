"""Shared lexical path identity rules for Scheduler capability boundaries."""

from __future__ import annotations

_WINDOWS_RESERVED_DEVICE_BASENAMES = frozenset(
    {
        "aux",
        "clock$",
        "con",
        "conin$",
        "conout$",
        "nul",
        "prn",
        *(f"com{suffix}" for suffix in "123456789¹²³"),
        *(f"lpt{suffix}" for suffix in "123456789¹²³"),
    }
)
_WINDOWS_ILLEGAL_COMPONENT_CHARACTERS = frozenset('<>:"\\|?*')


def _is_windows_reserved_device_component(component: str) -> bool:
    basename = component.split(".", 1)[0].rstrip(" ").casefold()
    return basename in _WINDOWS_RESERVED_DEVICE_BASENAMES


def _has_windows_illegal_component_character(component: str) -> bool:
    return any(
        ord(character) < 32
        or character in _WINDOWS_ILLEGAL_COMPONENT_CHARACTERS
        for character in component
    )


def normalized_path_has_windows_ambiguous_component(path: str) -> bool:
    """Return whether a slash-normalized path has a Windows-ambiguous part."""

    if not isinstance(path, str) or not path:
        raise ValueError("normalized path must be a non-empty string")
    return any(
        _has_windows_illegal_component_character(component)
        or (
            component not in {"", ".", ".."}
            and component.endswith((".", " "))
        )
        or _is_windows_reserved_device_component(component)
        for component in path.split("/")
    )


def normalized_path_identity(
    path: str,
    *,
    case_sensitive: bool,
) -> str:
    """Return the identity key for an already-normalized slash path."""

    if not isinstance(path, str) or not path:
        raise ValueError("normalized path must be a non-empty string")
    return path if case_sensitive else path.casefold()


def normalized_path_within(
    path: str,
    root: str,
    *,
    case_sensitive: bool,
) -> bool:
    """Return whether one normalized path is contained by one normalized root."""

    candidate = normalized_path_identity(path, case_sensitive=case_sensitive)
    normalized_root = normalized_path_identity(
        root,
        case_sensitive=case_sensitive,
    )
    if normalized_root == "." and not candidate.startswith("/"):
        return True
    return candidate == normalized_root or candidate.startswith(
        normalized_root.rstrip("/") + "/"
    )


def normalized_forbidden_paths_overlap(left: str, right: str) -> bool:
    """Conservatively compare two normalized deny paths case-insensitively."""

    return normalized_path_within(
        left,
        right,
        case_sensitive=False,
    ) or normalized_path_within(right, left, case_sensitive=False)


__all__ = [
    "normalized_forbidden_paths_overlap",
    "normalized_path_has_windows_ambiguous_component",
    "normalized_path_identity",
    "normalized_path_within",
]

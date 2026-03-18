"""Helpers for the first-mail project folder sync control action."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class ProjectFolderEntry:
    name: str
    path: str


@dataclass(slots=True)
class ProjectFolderRootListing:
    root_path: str
    available: bool
    entries: list[ProjectFolderEntry] = field(default_factory=list)
    error: str | None = None


def list_project_folders(roots: list[str]) -> list[ProjectFolderRootListing]:
    listings: list[ProjectFolderRootListing] = []
    for raw_root in roots:
        root_path = str(raw_root).strip()
        if not root_path:
            continue
        path = Path(root_path)
        if not path.exists():
            listings.append(ProjectFolderRootListing(root_path=root_path, available=False, error="path does not exist"))
            continue
        if not path.is_dir():
            listings.append(ProjectFolderRootListing(root_path=root_path, available=False, error="path is not a directory"))
            continue
        try:
            entries = [
                ProjectFolderEntry(name=child.name, path=str(child))
                for child in sorted(path.iterdir(), key=lambda item: item.name.lower())
                if child.is_dir()
            ]
        except OSError as exc:
            listings.append(ProjectFolderRootListing(root_path=root_path, available=False, error=str(exc)))
            continue
        listings.append(ProjectFolderRootListing(root_path=root_path, available=True, entries=entries))
    return listings


def build_project_folder_sync_body(listings: list[ProjectFolderRootListing], *, scanned_at: str) -> str:
    lines = [
        "Project folder sync completed. No task was created.",
        "",
        f"Scanned at: {scanned_at}",
        "",
    ]
    if not listings:
        lines.extend(
            [
                "No project roots are configured.",
                "",
                "To start a task, send a new [OC] or [CX] mail and fill Repo: manually.",
            ]
        )
        return "\n".join(lines)

    lines.append("Scanned roots:")
    for listing in listings:
        if listing.available:
            folder_label = "folder" if len(listing.entries) == 1 else "folders"
            lines.append(f"- {listing.root_path} | available | {len(listing.entries)} {folder_label}")
        else:
            lines.append(f"- {listing.root_path} | unavailable | {listing.error or 'unknown error'}")

    for listing in listings:
        lines.extend(["", listing.root_path])
        if not listing.available:
            lines.append(f"- unavailable | {listing.error or 'unknown error'}")
            continue
        if not listing.entries:
            lines.append("- (no folders found)")
            continue
        for entry in listing.entries:
            lines.append(f"- {entry.name} | {entry.path}")

    lines.extend(
        [
            "",
            "To start a task, send a new [OC] or [CX] mail and copy one path into Repo:.",
        ]
    )
    return "\n".join(lines)

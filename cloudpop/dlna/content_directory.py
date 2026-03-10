"""Minimal ContentDirectory implementation backed by local STRM files."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from xml.sax.saxutils import escape


@dataclass(frozen=True)
class DlnaNode:
    id: str
    parent_id: str
    title: str
    is_container: bool
    path: Path
    size: int = 0
    mime_type: str = "video/mp4"
    url: str = ""
    child_count: int = 0
    modified_at: str = ""
    art_url: str = ""


class DlnaLibrary:
    """Build a small in-memory directory tree from the local STRM output."""

    def __init__(self, root: Path, base_url: str) -> None:
        self._root = root
        self._base_url = base_url.rstrip("/")
        self._nodes: dict[str, DlnaNode] = {}
        self._children: dict[str, list[str]] = {}
        self.refresh()

    def refresh(self) -> None:
        root_title = self._root.name or "CloudPop"
        self._nodes = {
            "0": DlnaNode(
                id="0",
                parent_id="-1",
                title=root_title,
                is_container=True,
                path=self._root,
                child_count=self._count_children(self._root),
                modified_at=_format_mtime(self._root),
                art_url=f"{self._base_url}/dlna/icon.png",
            )
        }
        self._children = {"0": []}
        if not self._root.exists():
            return
        self._build_directory("0", self._root)

    def get_node(self, object_id: str) -> DlnaNode | None:
        return self._nodes.get(object_id)

    def get_children(self, object_id: str) -> list[DlnaNode]:
        return [self._nodes[node_id] for node_id in self._children.get(object_id, [])]

    def stats(self) -> dict[str, int]:
        containers = 0
        items = 0
        for node in self._nodes.values():
            if node.id == "0":
                continue
            if node.is_container:
                containers += 1
            else:
                items += 1
        return {"containers": containers, "items": items}

    def browse(
        self,
        object_id: str,
        browse_flag: str,
        starting_index: int,
        requested_count: int,
    ) -> tuple[str, int, int]:
        if browse_flag == "BrowseMetadata":
            node = self.get_node(object_id)
            if node is None:
                return didl_lite([]), 0, 0
            return didl_lite([node]), 1, 1

        children = self.get_children(object_id)
        total = len(children)
        end = total if requested_count == 0 else starting_index + requested_count
        sliced = children[starting_index:end]
        return didl_lite(sliced), len(sliced), total

    def search(
        self,
        object_id: str,
        criteria: str,
        starting_index: int,
        requested_count: int,
    ) -> tuple[str, int, int]:
        children = self.get_children(object_id)
        query = _extract_search_text(criteria)
        if query:
            lowered = query.lower()
            filtered = [node for node in children if lowered in node.title.lower()]
        else:
            filtered = children
        total = len(filtered)
        end = total if requested_count == 0 else starting_index + requested_count
        sliced = filtered[starting_index:end]
        return didl_lite(sliced), len(sliced), total

    def _build_directory(self, parent_id: str, directory: Path) -> None:
        entries = sorted(directory.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        for entry in entries:
            if entry.is_dir():
                node_id = self._node_id(entry)
                node = DlnaNode(
                    id=node_id,
                    parent_id=parent_id,
                    title=entry.name,
                    is_container=True,
                    path=entry,
                    child_count=self._count_children(entry),
                    modified_at=_format_mtime(entry),
                    art_url=f"{self._base_url}/dlna/icon.png",
                )
                self._add_node(node)
                self._build_directory(node_id, entry)
                continue

            if entry.suffix.lower() != ".strm":
                continue

            node_id = self._node_id(entry)
            stream_url = entry.read_text(encoding="utf-8").strip()
            node = DlnaNode(
                id=node_id,
                parent_id=parent_id,
                title=entry.stem,
                is_container=False,
                path=entry,
                size=entry.stat().st_size,
                mime_type=_infer_mime_from_url(stream_url),
                url=stream_url,
                modified_at=_format_mtime(entry),
                art_url=f"{self._base_url}/dlna/icon.png",
            )
            self._add_node(node)

    def _add_node(self, node: DlnaNode) -> None:
        self._nodes[node.id] = node
        self._children.setdefault(node.parent_id, []).append(node.id)
        self._children.setdefault(node.id, [])

    def _node_id(self, path: Path) -> str:
        relative = path.relative_to(self._root)
        return "0/" + "/".join(relative.parts)

    def _count_children(self, directory: Path) -> int:
        if not directory.exists():
            return 0
        count = 0
        for entry in directory.iterdir():
            if entry.is_dir() or entry.suffix.lower() == ".strm":
                count += 1
        return count


def didl_lite(nodes: list[DlnaNode]) -> str:
    parts = [
        '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
    ]
    for node in nodes:
        if node.is_container:
            parts.append(
                f'<container id="{escape(node.id)}" parentID="{escape(node.parent_id)}" restricted="1" searchable="1" childCount="{node.child_count}">'
                f"<dc:title>{escape(node.title)}</dc:title>"
                "<dc:creator>CloudPop</dc:creator>"
                f"<dc:date>{escape(node.modified_at)}</dc:date>"
                f'<upnp:albumArtURI>{escape(node.art_url)}</upnp:albumArtURI>'
                "<upnp:class>object.container.storageFolder</upnp:class>"
                "</container>"
            )
        else:
            parts.append(
                f'<item id="{escape(node.id)}" parentID="{escape(node.parent_id)}" restricted="1">'
                f"<dc:title>{escape(node.title)}</dc:title>"
                "<dc:creator>CloudPop</dc:creator>"
                f"<dc:date>{escape(node.modified_at)}</dc:date>"
                f'<upnp:albumArtURI>{escape(node.art_url)}</upnp:albumArtURI>'
                "<upnp:genre>VR</upnp:genre>"
                "<upnp:class>object.item.videoItem.movie</upnp:class>"
                f'<res size="{node.size}" protocolInfo="{escape(_protocol_info(node.mime_type))}">{escape(node.url)}</res>'
                "</item>"
            )
    parts.append("</DIDL-Lite>")
    return "".join(parts)


def _infer_mime_from_url(url: str) -> str:
    lower = url.lower()
    if lower.endswith(".mkv"):
        return "video/x-matroska"
    if lower.endswith(".ts"):
        return "video/mp2t"
    return "video/mp4"


def _protocol_info(mime_type: str) -> str:
    return (
        f"http-get:*:{mime_type}:"
        "DLNA.ORG_OP=01;"
        "DLNA.ORG_CI=0;"
        "DLNA.ORG_FLAGS=01700000000000000000000000000000"
    )


def _format_mtime(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    except OSError:
        return "1970-01-01T00:00:00Z"


def _extract_search_text(criteria: str) -> str:
    if not criteria or criteria == "*":
        return ""
    if "contains" in criteria:
        _, _, tail = criteria.partition("contains")
        return tail.strip().strip('"').strip("'")
    return criteria.strip().strip('"').strip("'")

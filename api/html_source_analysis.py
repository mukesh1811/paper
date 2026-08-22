"""Deterministic HTML facts used before Paper asks an intelligence model.

This module only measures the fetched source DOM and selects its dominant text
surface. It does not decide what a work means or generate reader text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any


MAX_HTML_TREE_DEPTH = 128
MAX_TRACKED_LINK_TARGETS = 10_000
MIN_OBVIOUS_SURFACE_CHARACTERS = 40_000
MIN_OBVIOUS_SURFACE_SHARE = 0.6
MAX_OBVIOUS_LINK_TEXT_RATIO = 0.12
MAX_OBVIOUS_NAV_TEXT_RATIO = 0.12
MIN_DOMINANT_SURFACE_SHARE = 0.6

_HTML_IGNORED_TAGS = {"head", "script", "style", "template", "noscript", "svg"}
_HTML_VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}
_HTML_BLOCK_TAGS = {
    "article",
    "aside",
    "blockquote",
    "div",
    "figcaption",
    "figure",
    "footer",
    "form",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "li",
    "main",
    "nav",
    "p",
    "pre",
    "section",
    "table",
    "td",
    "th",
}
_SURFACE_CONTAINER_TAGS = {
    "article",
    "blockquote",
    "div",
    "figure",
    "main",
    "pre",
    "section",
    "table",
    "td",
    "th",
}
_SURFACE_EXCLUDED_TAGS = {"a", "aside", "footer", "form", "header", "nav"}
_CONTENT_BLOCK_TAGS = {
    "blockquote",
    "dd",
    "dt",
    "figcaption",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "li",
    "p",
    "pre",
    "td",
    "th",
}
_FALLBACK_BLOCK_TAGS = {
    "article",
    "aside",
    "body",
    "div",
    "document",
    "figure",
    "font",
    "footer",
    "header",
    "main",
    "nav",
    "section",
    "table",
    "tr",
}


@dataclass(frozen=True)
class HTMLSourceAnalysis:
    """Exact visible DOM data and stable measurements for one HTML source."""

    title: str
    structure: dict[str, Any]
    reading_surface: dict[str, Any]
    reading_surface_text: str
    dom_nodes: tuple["HTMLDOMNode", ...]
    source_blocks: tuple["HTMLSourceBlock", ...]
    node_selectors: dict[str, str]

    @property
    def has_obvious_reading_surface(self) -> bool:
        """Return true only for a large, dominant, low-navigation source body."""

        surface_characters = self.reading_surface["visible_text_characters"]
        page_characters = self.structure["visible_text_characters"]
        return (
            surface_characters >= MIN_OBVIOUS_SURFACE_CHARACTERS
            and surface_characters / page_characters >= MIN_OBVIOUS_SURFACE_SHARE
            and self.reading_surface["link_text_ratio"] <= MAX_OBVIOUS_LINK_TEXT_RATIO
            and self.reading_surface["nav_text_ratio"] <= MAX_OBVIOUS_NAV_TEXT_RATIO
        )


@dataclass(frozen=True)
class HTMLDOMNode:
    """A visible source node, with only attributes useful to reading judgment."""

    id: str
    parent_id: str | None
    tag: str
    role: str | None


@dataclass(frozen=True)
class HTMLSourceBlock:
    """One complete visible content block, cited by the model if needed."""

    id: str
    node_id: str
    tag: str
    text: str
    link_count: int


@dataclass
class _HTMLNode:
    """A bounded source DOM node used only to measure a reading surface."""

    id: str
    parent_id: str | None
    tag: str
    depth: int
    role: str | None = None
    content: list[Any] = field(default_factory=list)
    visible_text_characters: int = 0
    link_text_characters: int = 0
    nav_text_characters: int = 0
    main_text_characters: int = 0
    article_text_characters: int = 0
    main_count: int = 0
    article_count: int = 0
    section_count: int = 0
    nav_count: int = 0
    aside_count: int = 0
    link_count: int = 0
    heading_counts: dict[str, int] = field(
        default_factory=lambda: {f"h{level}": 0 for level in range(1, 7)}
    )


class _HTMLSourceParser(HTMLParser):
    """Build bounded source DOM facts and a text-bearing reading surface."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _HTMLNode(id="n0", parent_id=None, tag="document", depth=0)
        self._stack = [self.root]
        self._nodes = [self.root]
        self._next_node_number = 1
        self._ignored_depth = 0
        self._untracked_depth = 0
        self._title_depth = 0
        self._main_depth = 0
        self._article_depth = 0
        self._main_open_tags: list[str] = []
        self._article_open_tags: list[str] = []
        self._nav_depth = 0
        self._anchor_depth = 0
        self.title_parts: list[str] = []
        self._link_targets: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "title":
            self._title_depth += 1
        if tag in _HTML_IGNORED_TAGS:
            self._ignored_depth += 1
            return
        if self._ignored_depth:
            return
        if self._untracked_depth:
            if tag not in _HTML_VOID_TAGS:
                self._untracked_depth += 1
            return
        if len(self._stack) >= MAX_HTML_TREE_DEPTH:
            if tag not in _HTML_VOID_TAGS:
                self._untracked_depth = 1
            return

        attrs_by_name = {name.lower(): value for name, value in attrs}
        role = attrs_by_name.get("role")
        role = role.lower().strip() if role and role.lower().strip() in {"article", "document", "main"} else None
        node = _HTMLNode(
            id=f"n{self._next_node_number}",
            parent_id=self._stack[-1].id,
            tag=tag,
            depth=len(self._stack),
            role=role,
        )
        self._next_node_number += 1
        self._stack[-1].content.append(node)
        self._nodes.append(node)
        if tag not in _HTML_VOID_TAGS:
            self._stack.append(node)
            active_nodes = self._stack
        else:
            active_nodes = [*self._stack, node]

        if tag == "main" or role == "main":
            self._main_depth += 1
            self._main_open_tags.append(tag)
            for active in active_nodes:
                active.main_count += 1
        if tag == "article" or role in {"article", "document"}:
            self._article_depth += 1
            self._article_open_tags.append(tag)
            for active in active_nodes:
                active.article_count += 1
        if tag == "section":
            for active in active_nodes:
                active.section_count += 1
        if tag == "nav":
            self._nav_depth += 1
            for active in active_nodes:
                active.nav_count += 1
        if tag == "aside":
            for active in active_nodes:
                active.aside_count += 1
        if tag == "a":
            self._anchor_depth += 1
            for active in active_nodes:
                active.link_count += 1
            href = attrs_by_name.get("href")
            if href and len(self._link_targets) < MAX_TRACKED_LINK_TARGETS:
                self._link_targets.add(href)
        if tag in self.root.heading_counts:
            for active in active_nodes:
                active.heading_counts[tag] += 1

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title" and self._title_depth:
            self._title_depth -= 1
        if tag in _HTML_IGNORED_TAGS and self._ignored_depth:
            self._ignored_depth -= 1
            return
        if self._ignored_depth:
            return
        if self._untracked_depth:
            if tag not in _HTML_VOID_TAGS:
                self._untracked_depth -= 1
            return
        if tag in _HTML_VOID_TAGS:
            return

        if self._main_open_tags and self._main_open_tags[-1] == tag:
            self._main_open_tags.pop()
            self._main_depth -= 1
        if self._article_open_tags and self._article_open_tags[-1] == tag:
            self._article_open_tags.pop()
            self._article_depth -= 1
        if tag == "nav" and self._nav_depth:
            self._nav_depth -= 1
        if tag == "a" and self._anchor_depth:
            self._anchor_depth -= 1
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == tag:
                del self._stack[index:]
                break

    def handle_data(self, data: str) -> None:
        if self._title_depth:
            self.title_parts.append(data)
        if self._ignored_depth:
            return

        self._stack[-1].content.append(data)
        visible_characters = len(" ".join(data.split()))
        for active in self._stack:
            active.visible_text_characters += visible_characters
            if self._anchor_depth:
                active.link_text_characters += visible_characters
            if self._nav_depth:
                active.nav_text_characters += visible_characters
            if self._main_depth:
                active.main_text_characters += visible_characters
            if self._article_depth:
                active.article_text_characters += visible_characters

    def structure(self) -> dict[str, Any]:
        root = self.root
        visible_characters = root.visible_text_characters
        return {
            "has_main": bool(root.main_count),
            "has_article": bool(root.article_count),
            "section_count": root.section_count,
            "nav_count": root.nav_count,
            "aside_count": root.aside_count,
            "heading_counts": root.heading_counts,
            "link_count": root.link_count,
            "unique_link_target_count": len(self._link_targets),
            "visible_text_characters": visible_characters,
            "link_text_ratio": _ratio(root.link_text_characters, visible_characters),
            "nav_text_ratio": _ratio(root.nav_text_characters, visible_characters),
            "main_text_ratio": _ratio(root.main_text_characters, visible_characters),
            "article_text_ratio": _ratio(root.article_text_characters, visible_characters),
        }

    def reading_surface(self) -> tuple[str, _HTMLNode]:
        candidates = [node for node in self._nodes if node.visible_text_characters]
        article_nodes = [
            node for node in candidates if node.tag == "article" or node.role in {"article", "document"}
        ]
        if article_nodes:
            return "article_landmark", max(article_nodes, key=_surface_sort_key)
        main_nodes = [node for node in candidates if node.tag == "main" or node.role == "main"]
        if main_nodes:
            return "main_landmark", max(main_nodes, key=_surface_sort_key)

        generic_nodes = [
            node
            for node in candidates
            if node is not self.root
            and node.tag not in _SURFACE_EXCLUDED_TAGS
            and node.tag not in {"body", "head", "html"}
        ]
        if generic_nodes:
            containers = [node for node in generic_nodes if node.tag in _SURFACE_CONTAINER_TAGS]
            candidate = _deepest_near_largest_surface(containers or generic_nodes)
            if candidate.visible_text_characters / self.root.visible_text_characters >= MIN_DOMINANT_SURFACE_SHARE:
                return "largest_text_subtree", candidate

        body_nodes = [node for node in candidates if node.tag == "body"]
        return "body_fallback", max(body_nodes or [self.root], key=_surface_sort_key)

    def surface_facts(self, selection: str, node: _HTMLNode) -> dict[str, Any]:
        visible_characters = node.visible_text_characters
        return {
            "selection": selection,
            "tag": node.tag,
            "role": node.role,
            "visible_text_characters": visible_characters,
            "link_text_ratio": _ratio(node.link_text_characters, visible_characters),
            "nav_text_ratio": _ratio(node.nav_text_characters, visible_characters),
            "heading_counts": node.heading_counts,
        }

    def dom_nodes(self, source_blocks: tuple[HTMLSourceBlock, ...]) -> tuple[HTMLDOMNode, ...]:
        """Return only the source-block hierarchy, not every inline DOM node."""

        node_by_id = {node.id: node for node in self._nodes}
        included_ids: set[str] = set()
        for block in source_blocks:
            node = node_by_id[block.node_id]
            while node.parent_id is not None:
                included_ids.add(node.id)
                node = node_by_id[node.parent_id]

        return tuple(
            HTMLDOMNode(
                id=node.id,
                parent_id=node.parent_id,
                tag=node.tag,
                role=node.role,
            )
            for node in self._nodes[1:]
            if node.id in included_ids
        )

    def source_blocks(self) -> tuple[HTMLSourceBlock, ...]:
        """Return every visible word once, grouped at a content boundary."""

        groups: dict[str, dict[str, Any]] = {}

        def visit(node: _HTMLNode, ancestors: list[_HTMLNode]) -> None:
            for item in node.content:
                if isinstance(item, _HTMLNode):
                    visit(item, [*ancestors, item])
                    continue
                if not item.strip():
                    continue
                block_node = _block_container(ancestors)
                group = groups.setdefault(
                    block_node.id,
                    {
                        "id": f"b{len(groups) + 1}",
                        "node": block_node,
                        "parts": [],
                        "anchor_ids": set(),
                    },
                )
                group["parts"].append(item)
                for ancestor in ancestors:
                    if ancestor.tag == "a":
                        group["anchor_ids"].add(ancestor.id)

        visit(self.root, [self.root])
        return tuple(
            HTMLSourceBlock(
                id=group["id"],
                node_id=group["node"].id,
                tag=group["node"].tag,
                text=re.sub(r"\s+", " ", "".join(group["parts"])).strip(),
                link_count=len(group["anchor_ids"]),
            )
            for group in groups.values()
        )

    def node_selectors(self) -> dict[str, str]:
        """Address each tracked source node with a deterministic CSS path.

        The path deliberately uses only element names and sibling positions. It
        does not trust page-provided IDs or classes, and it can be checked again
        against the fetched source when a reader block is validated.
        """

        selectors: dict[str, str] = {}

        def visit(node: _HTMLNode, parent_selector: str) -> None:
            children = [item for item in node.content if isinstance(item, _HTMLNode)]
            seen_by_tag: dict[str, int] = {}
            for child in children:
                seen_by_tag[child.tag] = seen_by_tag.get(child.tag, 0) + 1
                component = f"{child.tag}:nth-of-type({seen_by_tag[child.tag]})"
                selector = f"{parent_selector} > {component}" if parent_selector else component
                selectors[child.id] = selector
                visit(child, selector)

        visit(self.root, "")
        return selectors


def analyze_html_source(payload: bytes) -> HTMLSourceAnalysis:
    """Measure a fetched HTML source without making a semantic model decision."""

    try:
        html = payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        html = payload.decode("cp1252", errors="replace")

    parser = _HTMLSourceParser()
    parser.feed(html)
    parser.close()
    selection, surface_node = parser.reading_surface()
    source_blocks = parser.source_blocks()
    return HTMLSourceAnalysis(
        title=re.sub(r"\s+", " ", "".join(parser.title_parts)).strip(),
        structure=parser.structure(),
        reading_surface=parser.surface_facts(selection, surface_node),
        reading_surface_text=_node_text(surface_node),
        dom_nodes=parser.dom_nodes(source_blocks),
        source_blocks=source_blocks,
        node_selectors=parser.node_selectors(),
    )


def _block_container(ancestors: list[_HTMLNode]) -> _HTMLNode:
    """Choose the nearest content boundary, with an old-markup fallback."""

    for node in reversed(ancestors):
        if node.tag in _CONTENT_BLOCK_TAGS:
            return node
    for node in reversed(ancestors):
        if node.tag in _FALLBACK_BLOCK_TAGS:
            return node
    return ancestors[-1]


def _surface_sort_key(node: _HTMLNode) -> tuple[int, int, int]:
    return (
        node.visible_text_characters - node.link_text_characters,
        node.visible_text_characters,
        node.depth,
    )


def _deepest_near_largest_surface(nodes: list[_HTMLNode]) -> _HTMLNode:
    largest_weight = max(_surface_weight(node) for node in nodes)
    near_largest = [node for node in nodes if _surface_weight(node) >= largest_weight * 0.9]
    return max(near_largest, key=lambda node: (node.depth, *_surface_sort_key(node)))


def _surface_weight(node: _HTMLNode) -> int:
    return node.visible_text_characters - node.link_text_characters


def _node_text(node: _HTMLNode) -> str:
    """Join exact source text with only DOM block-boundary whitespace added."""

    parts: list[str] = []
    for item in node.content:
        if isinstance(item, _HTMLNode):
            text = _node_text(item)
            if not text:
                continue
            if item.tag in _HTML_BLOCK_TAGS:
                parts.append("\n")
            parts.append(text)
            if item.tag in _HTML_BLOCK_TAGS:
                parts.append("\n")
        else:
            parts.append(item)
    return re.sub(r"\s+", " ", "".join(parts)).strip()


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 3) if denominator else 0.0

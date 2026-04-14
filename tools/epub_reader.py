"""
EPUB Reader — extract plain text from .epub files.

Uses only Python stdlib (zipfile + html.parser). No extra dependencies.
Reads the spine order from content.opf and concatenates chapter text.

Usage:
    python -m tools.epub_reader "novel.epub"
    python -m tools.epub_reader "novel.epub" --output "output.txt"
"""

import argparse
import os
import sys
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from typing import List, Optional
from xml.etree import ElementTree as ET


class _HTMLTextExtractor(HTMLParser):
    """Strip HTML tags, keep text content."""

    def __init__(self):
        super().__init__()
        self._pieces: List[str] = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip = True
        elif tag in ("p", "div", "br", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr"):
            self._pieces.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = False
        elif tag in ("p", "div", "h1", "h2", "h3", "h4", "h5", "h6"):
            self._pieces.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self._pieces.append(data)

    def get_text(self) -> str:
        text = "".join(self._pieces)
        # Collapse multiple blank lines into at most two
        lines = text.splitlines()
        result = []
        blank_count = 0
        for line in lines:
            stripped = line.strip()
            if not stripped:
                blank_count += 1
                if blank_count <= 2:
                    result.append("")
            else:
                blank_count = 0
                result.append(stripped)
        return "\n".join(result).strip()


def _html_to_text(html: str) -> str:
    """Convert HTML/XHTML content to plain text."""
    parser = _HTMLTextExtractor()
    parser.feed(html)
    return parser.get_text()


def read_epub(epub_path: Path) -> str:
    """
    Read an EPUB file and return its text content in spine order.

    Args:
        epub_path: Path to the .epub file

    Returns:
        Plain text content of the book
    """
    epub_path = Path(epub_path)
    if not epub_path.exists():
        raise FileNotFoundError(f"EPUB file not found: {epub_path}")
    if not zipfile.is_zipfile(epub_path):
        raise ValueError(f"Not a valid EPUB (ZIP) file: {epub_path}")

    with zipfile.ZipFile(epub_path, "r") as zf:
        # Find content.opf via META-INF/container.xml
        opf_path = _find_opf_path(zf)
        opf_dir = os.path.dirname(opf_path) if "/" in opf_path else ""

        # Parse content.opf for manifest and spine
        opf_xml = zf.read(opf_path).decode("utf-8")
        manifest, spine_order = _parse_opf(opf_xml)

        # Read chapters in spine order
        chapters: List[str] = []
        for idref in spine_order:
            if idref not in manifest:
                continue
            href = manifest[idref]
            # Resolve relative path
            full_path = f"{opf_dir}/{href}" if opf_dir else href
            # Normalize path separators
            full_path = full_path.replace("\\", "/")

            if full_path not in zf.namelist():
                # Try without opf_dir prefix (some epubs use absolute paths)
                if href in zf.namelist():
                    full_path = href
                else:
                    continue

            try:
                content = zf.read(full_path).decode("utf-8")
                text = _html_to_text(content)
                if text.strip():
                    chapters.append(text)
            except (KeyError, UnicodeDecodeError):
                continue

    return "\n\n---\n\n".join(chapters)


def _find_opf_path(zf: zipfile.ZipFile) -> str:
    """Find the content.opf path from container.xml."""
    container_path = "META-INF/container.xml"
    if container_path not in zf.namelist():
        # Fallback: search for .opf file
        for name in zf.namelist():
            if name.endswith(".opf"):
                return name
        raise ValueError("Cannot find content.opf in EPUB")

    container_xml = zf.read(container_path).decode("utf-8")
    root = ET.fromstring(container_xml)

    # Handle namespace
    ns = {"container": "urn:oasis:names:tc:opendocument:xmlns:container"}
    rootfile = root.find(".//container:rootfile", ns)
    if rootfile is None:
        # Try without namespace
        rootfile = root.find(".//{*}rootfile")
    if rootfile is None:
        raise ValueError("Cannot find rootfile in container.xml")

    return rootfile.attrib["full-path"]


def _parse_opf(opf_xml: str):
    """Parse content.opf, return (manifest dict, spine order list)."""
    root = ET.fromstring(opf_xml)

    # Detect OPF namespace
    ns_opf = ""
    if root.tag.startswith("{"):
        ns_opf = root.tag.split("}")[0] + "}"

    # Build manifest: id -> href
    manifest = {}
    manifest_el = root.find(f"{ns_opf}manifest")
    if manifest_el is not None:
        for item in manifest_el.findall(f"{ns_opf}item"):
            item_id = item.attrib.get("id", "")
            href = item.attrib.get("href", "")
            media_type = item.attrib.get("media-type", "")
            if "html" in media_type or "xml" in media_type:
                manifest[item_id] = href

    # Build spine order
    spine_order = []
    spine_el = root.find(f"{ns_opf}spine")
    if spine_el is not None:
        for itemref in spine_el.findall(f"{ns_opf}itemref"):
            idref = itemref.attrib.get("idref", "")
            if idref:
                spine_order.append(idref)

    return manifest, spine_order


def save_epub_text(epub_path: Path, output_path: Optional[Path] = None) -> Path:
    """
    Read EPUB and save as plain text file.

    Args:
        epub_path: Path to .epub file
        output_path: Output .txt path (default: same name with .txt extension)

    Returns:
        Path to the output text file
    """
    text = read_epub(epub_path)
    if output_path is None:
        output_path = epub_path.with_suffix(".txt")
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(text)
    return output_path


def main():
    parser = argparse.ArgumentParser(description="Extract plain text from EPUB files")
    parser.add_argument("epub", type=Path, help="Path to .epub file")
    parser.add_argument("--output", "-o", type=Path, default=None, help="Output .txt path (default: same name .txt)")
    args = parser.parse_args()

    output = save_epub_text(args.epub, args.output)
    print(f"Extracted text saved to: {output}")


if __name__ == "__main__":
    main()

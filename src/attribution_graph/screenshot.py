"""Screenshot capture: what a viewer would have seen, and where.

## A screenshot is a rendering, not a capture

This distinction is the whole design and getting it wrong would undermine the
evidence package.

The bytes on the wire are the **primary** evidence: hash them and anyone can
verify the file they hold is the file that was served. A screenshot cannot be
verified that way. It is the output of a browser rendering those bytes, at one
viewport size, with one font stack, at one moment, possibly after JavaScript
that fetched further resources.

So a screenshot is recorded as a **derived artifact** with its own hash and its
own place in the chain, explicitly labelled as a rendering. It is not offered as
proof of what was served. What it is good for:

- showing content that only exists after JavaScript runs, which the raw HTML
  does not contain
- showing *where on the page* a finding appeared, which raw HTML conveys poorly
- showing what a human visitor would actually have seen, which matters when the
  question is what the operator represented to the public

## Element location

"The email was on the page" is weak. "The email appeared in the footer, at
(412, 2180), inside `footer > div.contact > a`, reading 'Contact: …'" is
something a reviewer can check against the screenshot and the DOM together.

Each finding gets an ``ElementLocation``: CSS selector, DOM path, bounding box,
and the surrounding text. All four are recorded because each fails differently —
selectors break on reflow, coordinates break on viewport change, text survives
both but does not say where.

## Annotation is a second image, never an edit

Drawing a box around a finding alters the image. The unannotated capture is
preserved and hashed first; any highlighted version is a separate derived file
referencing its parent. A single annotated image with no original is not
evidence, it is an illustration.

## Degradation

Screenshots need a headless browser. When one is unavailable the run records
that a screenshot was **not captured**, with the reason, rather than failing or
silently omitting it. An absent screenshot must be distinguishable from a page
that rendered blank.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path


class ShotStatus(StrEnum):
    CAPTURED = "captured"
    NO_BROWSER = "no_browser_available"
    FAILED = "capture_failed"
    SKIPPED = "skipped_by_policy"
    TIMEOUT = "timeout"


@dataclass
class ElementLocation:
    """Where on the page a finding appeared.

    Four locators, because each fails differently: selectors break on reflow,
    coordinates break on a viewport change, the DOM path breaks on markup
    edits, and surrounding text survives all three but says nothing about
    position.
    """

    selector: str = ""
    dom_path: str = ""
    x: int | None = None
    y: int | None = None
    width: int | None = None
    height: int | None = None
    surrounding_text: str = ""
    region: str = ""          # header | main | footer | sidebar | unknown

    def describe(self) -> str:
        parts = []
        if self.region:
            parts.append(f"in the {self.region}")
        if self.x is not None and self.y is not None:
            parts.append(f"at ({self.x}, {self.y})")
        if self.selector:
            parts.append(f"matching `{self.selector}`")
        return ", ".join(parts) or "location not determined"

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v not in ("", None)}


@dataclass
class Screenshot:
    """One rendering of one page, with its own hash and chain position."""

    url: str
    captured_at: datetime
    status: ShotStatus
    image_path: str = ""
    image_sha256: str = ""
    image_bytes: int = 0
    viewport: str = ""
    full_page: bool = True
    renderer: str = ""
    page_title: str = ""
    findings: list[ElementLocation] = field(default_factory=list)
    annotated_path: str = ""
    annotated_sha256: str = ""
    parent_body_sha256: str = ""     # the wire capture this renders
    note: str = ""
    sequence: int = 0
    prev_hash: str = ""
    entry_hash: str = ""

    #: Recorded on every screenshot so no consumer can mistake it for the
    #: primary artifact.
    KIND = "derived_rendering"

    def compute_entry_hash(self) -> str:
        payload = json.dumps({
            "url": self.url,
            "captured_at": self.captured_at.isoformat(),
            "status": self.status.value,
            "image_sha256": self.image_sha256,
            "image_bytes": self.image_bytes,
            "parent_body_sha256": self.parent_body_sha256,
            "sequence": self.sequence,
            "prev_hash": self.prev_hash,
        }, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()

    @property
    def captured(self) -> bool:
        return self.status is ShotStatus.CAPTURED

    def to_dict(self) -> dict:
        d = {
            "kind": self.KIND,
            "url": self.url,
            "captured_at": self.captured_at.isoformat(),
            "status": self.status.value,
            "sequence": self.sequence,
            "prev_hash": self.prev_hash,
            "entry_hash": self.entry_hash,
        }
        if self.captured:
            d.update({
                "image_path": self.image_path,
                "image_sha256": self.image_sha256,
                "image_bytes": self.image_bytes,
                "viewport": self.viewport,
                "full_page": self.full_page,
                "renderer": self.renderer,
                "page_title": self.page_title,
                "parent_body_sha256": self.parent_body_sha256,
            })
            if self.annotated_path:
                d["annotated"] = {
                    "path": self.annotated_path,
                    "sha256": self.annotated_sha256,
                    "note": "derived from the capture above; the unannotated "
                            "image is the one to verify",
                }
        if self.findings:
            d["findings"] = [f.to_dict() for f in self.findings]
        if self.note:
            d["note"] = self.note
        return d

    def render_text(self) -> str:
        """Plain-text block for the exported log."""
        lines = [
            f"URL          {self.url}",
            f"Captured at  {self.captured_at.isoformat()}",
            f"Status       {self.status.value}",
        ]
        if self.captured:
            lines += [
                f"Image        {self.image_path}",
                f"SHA-256      {self.image_sha256}",
                f"Size         {self.image_bytes} bytes",
                f"Viewport     {self.viewport}{' (full page)' if self.full_page else ''}",
                f"Renderer     {self.renderer}",
            ]
            if self.page_title:
                lines.append(f"Page title   {self.page_title}")
            if self.parent_body_sha256:
                lines.append(f"Renders      body sha256 {self.parent_body_sha256}")
        elif self.note:
            lines.append(f"Reason       {self.note}")

        if self.findings:
            lines.append("Findings")
            for f in self.findings:
                lines.append(f"  - {f.describe()}")
                if f.surrounding_text:
                    txt = f.surrounding_text.strip().replace("\n", " ")[:120]
                    lines.append(f"    text: {txt}")
        lines += [
            f"Chain        seq {self.sequence}, prev {self.prev_hash[:16] or '(genesis)'}",
            f"Entry hash   {self.entry_hash}",
        ]
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Renderers
# --------------------------------------------------------------------------- #

def available_renderer() -> tuple[str, str] | None:
    """The first usable headless renderer, as ``(name, executable)``.

    Checked at call time rather than import, so a machine that gains a browser
    later does not need the package reinstalled.
    """
    # Importability is not availability: the playwright package installs
    # without browsers, and treating the import as a renderer produced failures
    # instead of a clean NO_BROWSER outcome.
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            path = pw.chromium.executable_path
        if path and Path(path).exists():
            return ("playwright", path)
    except Exception:
        pass
    for name, exe in (("chromium", "chromium"), ("chromium", "chromium-browser"),
                      ("chrome", "google-chrome"), ("firefox", "firefox")):
        path = shutil.which(exe)
        if path:
            return (name, path)
    return None


#: JavaScript run in-page to locate findings and read the DOM context.
#: Kept here rather than inlined so it can be reviewed as a unit -- it runs on a
#: page the subject controls, so it reads only and writes nothing.
_LOCATE_JS = r"""
(needles) => {
  const out = [];
  const regionOf = (el) => {
    for (let n = el; n; n = n.parentElement) {
      const t = (n.tagName || '').toLowerCase();
      if (t === 'footer') return 'footer';
      if (t === 'header') return 'header';
      if (t === 'nav') return 'nav';
      if (t === 'aside') return 'sidebar';
      if (t === 'main' || t === 'article') return 'main';
    }
    return 'unknown';
  };
  const pathOf = (el) => {
    const p = [];
    for (let n = el; n && n.nodeType === 1 && p.length < 8; n = n.parentElement) {
      let s = n.tagName.toLowerCase();
      if (n.id) { s += '#' + n.id; p.unshift(s); break; }
      if (n.className && typeof n.className === 'string') {
        const c = n.className.trim().split(/\s+/)[0];
        if (c) s += '.' + c;
      }
      p.unshift(s);
    }
    return p.join(' > ');
  };
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const node = walker.currentNode;
    const text = node.nodeValue || '';
    for (const needle of needles) {
      if (!needle || text.indexOf(needle) === -1) continue;
      const el = node.parentElement;
      if (!el) continue;
      const r = el.getBoundingClientRect();
      out.push({
        needle: needle,
        selector: pathOf(el),
        dom_path: pathOf(el),
        x: Math.round(r.left + window.scrollX),
        y: Math.round(r.top + window.scrollY),
        width: Math.round(r.width),
        height: Math.round(r.height),
        region: regionOf(el),
        surrounding_text: text.trim().slice(0, 300)
      });
    }
  }
  return out.slice(0, 50);
}
"""


class ScreenshotCapturer:
    """Captures renderings into an evidence directory.

    ``needles`` are the strings a finding consists of — an email, a publisher
    ID, a name. Each is located in the DOM so the record says where it appeared
    rather than merely that it did.
    """

    def __init__(
        self,
        root: Path,
        *,
        viewport: tuple[int, int] = (1440, 900),
        full_page: bool = True,
        timeout_ms: int = 20_000,
        enabled: bool = True,
    ) -> None:
        self.root = Path(root)
        self.dir = self.root / "screenshots"
        self.viewport = viewport
        self.full_page = full_page
        self.timeout_ms = timeout_ms
        self.enabled = enabled
        self.shots: list[Screenshot] = []
        self._last_hash = ""

    # -- capture ------------------------------------------------------------ #

    def capture(
        self,
        url: str,
        *,
        needles: list[str] | None = None,
        parent_body_sha256: str = "",
        note: str = "",
    ) -> Screenshot:
        now = datetime.now(timezone.utc)
        seq = len(self.shots) + 1

        if not self.enabled:
            return self._record(Screenshot(
                url=url, captured_at=now, status=ShotStatus.SKIPPED,
                sequence=seq, note=note or "screenshots disabled for this run",
                parent_body_sha256=parent_body_sha256))

        renderer = available_renderer()
        if renderer is None:
            return self._record(Screenshot(
                url=url, captured_at=now, status=ShotStatus.NO_BROWSER,
                sequence=seq, parent_body_sha256=parent_body_sha256,
                note="no headless browser found. Install with "
                     "`pip install playwright && playwright install chromium`, "
                     "or apt-install chromium. An absent screenshot is not a "
                     "blank page."))

        name, exe = renderer
        self.dir.mkdir(parents=True, exist_ok=True)
        try:
            if name == "playwright":
                shot = self._via_playwright(url, needles or [], now, seq)
            else:
                shot = self._via_cli(url, exe, name, now, seq)
        except Exception as e:  # a failed screenshot must not end the run
            return self._record(Screenshot(
                url=url, captured_at=now, status=ShotStatus.FAILED,
                sequence=seq, parent_body_sha256=parent_body_sha256,
                note=f"{type(e).__name__}: {e}"[:300]))

        shot.parent_body_sha256 = parent_body_sha256
        if note:
            shot.note = note
        return self._record(shot)

    def _via_playwright(self, url, needles, now, seq) -> Screenshot:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                page = browser.new_page(
                    viewport={"width": self.viewport[0], "height": self.viewport[1]})
                page.goto(url, timeout=self.timeout_ms, wait_until="load")
                title = page.title()

                located: list[ElementLocation] = []
                if needles:
                    for hit in page.evaluate(_LOCATE_JS, needles) or []:
                        located.append(ElementLocation(
                            selector=hit.get("selector", ""),
                            dom_path=hit.get("dom_path", ""),
                            x=hit.get("x"), y=hit.get("y"),
                            width=hit.get("width"), height=hit.get("height"),
                            region=hit.get("region", "unknown"),
                            surrounding_text=hit.get("surrounding_text", "")))

                # The unannotated image is hashed first and is the one to
                # verify. Any highlighted version is a separate derived file.
                path = self.dir / f"{seq:04d}.png"
                page.screenshot(path=str(path), full_page=self.full_page)
            finally:
                browser.close()

        raw = path.read_bytes()
        return Screenshot(
            url=url, captured_at=now, status=ShotStatus.CAPTURED,
            image_path=str(path.relative_to(self.root)),
            image_sha256=hashlib.sha256(raw).hexdigest(),
            image_bytes=len(raw),
            viewport=f"{self.viewport[0]}x{self.viewport[1]}",
            full_page=self.full_page, renderer="playwright/chromium",
            page_title=title, findings=located, sequence=seq)

    def _via_cli(self, url, exe, name, now, seq) -> Screenshot:
        """Headless-browser CLI fallback. No DOM access, so no element
        locations — the image is captured, the positions are not."""
        path = self.dir / f"{seq:04d}.png"
        cmd = [exe, "--headless", "--disable-gpu", "--no-sandbox",
               f"--window-size={self.viewport[0]},{self.viewport[1]}",
               f"--screenshot={path}", url]
        subprocess.run(cmd, check=True, capture_output=True,  # noqa: S603
                       timeout=self.timeout_ms / 1000)
        raw = path.read_bytes()
        return Screenshot(
            url=url, captured_at=now, status=ShotStatus.CAPTURED,
            image_path=str(path.relative_to(self.root)),
            image_sha256=hashlib.sha256(raw).hexdigest(),
            image_bytes=len(raw),
            viewport=f"{self.viewport[0]}x{self.viewport[1]}",
            full_page=False, renderer=f"{name} (CLI)", sequence=seq,
            note="captured via browser CLI: no DOM access, so element "
                 "locations were not recorded")

    def _record(self, shot: Screenshot) -> Screenshot:
        shot.prev_hash = self._last_hash
        shot.entry_hash = shot.compute_entry_hash()
        self._last_hash = shot.entry_hash
        self.shots.append(shot)
        return shot

    # -- verification ------------------------------------------------------- #

    def verify(self) -> tuple[bool, list[str]]:
        """Re-hash every image and re-check the chain."""
        problems: list[str] = []
        prev = ""
        root = self.root.resolve()

        for shot in self.shots:
            if shot.captured:
                candidate = (self.root / shot.image_path).resolve()
                try:
                    candidate.relative_to(root)
                except ValueError:
                    problems.append(
                        f"seq {shot.sequence}: image_path escapes the package")
                    prev = shot.entry_hash
                    continue
                if not candidate.exists():
                    problems.append(f"seq {shot.sequence}: image missing")
                else:
                    actual = hashlib.sha256(candidate.read_bytes()).hexdigest()
                    if actual != shot.image_sha256:
                        problems.append(
                            f"seq {shot.sequence}: image hash mismatch")

            if shot.prev_hash != prev:
                problems.append(f"seq {shot.sequence}: chain break")
            if shot.compute_entry_hash() != shot.entry_hash:
                problems.append(f"seq {shot.sequence}: entry hash mismatch")
            prev = shot.entry_hash

        return (not problems), problems

    # -- export -------------------------------------------------------------- #

    def export_text(self, path: Path | str | None = None) -> str:
        """Plain-text screenshot log.

        Written as text so it can be read, printed, attached to a filing or
        diffed without this package installed. An evidence record that needs
        the tool that produced it to be legible is a weaker record.
        """
        captured = [s for s in self.shots if s.captured]
        missing = [s for s in self.shots if not s.captured]

        lines = [
            "=" * 78,
            "SCREENSHOT EVIDENCE LOG",
            "=" * 78,
            "",
            f"Generated       {datetime.now(timezone.utc).isoformat()}",
            f"Package root    {self.root}",
            f"Screenshots     {len(self.shots)} recorded "
            f"({len(captured)} captured, {len(missing)} not)",
            f"Viewport        {self.viewport[0]}x{self.viewport[1]}"
            f"{' full-page' if self.full_page else ''}",
            "",
            "A screenshot is a RENDERING, not a capture. The bytes on the wire",
            "are the primary evidence and can be verified by hash; a screenshot",
            "is what one browser displayed from those bytes at one moment, at",
            "one viewport size. It shows JavaScript-rendered content the raw",
            "HTML does not contain, and it shows where on the page a finding",
            "appeared. It is not offered as proof of what was served.",
            "",
        ]

        ok, problems = self.verify()
        lines += ["-" * 78,
                  f"CHAIN VERIFICATION: {'PASS' if ok else 'FAIL'}",
                  "-" * 78, ""]
        if problems:
            lines += [f"  {p}" for p in problems] + [""]
        else:
            lines += ["  Every image re-hashes to its recorded digest and the",
                      "  chain is intact. Removing or reordering any entry",
                      "  breaks every subsequent hash.", ""]

        for shot in self.shots:
            lines += ["-" * 78, f"[{shot.sequence:04d}]", "-" * 78, "",
                      shot.render_text(), ""]

        if missing:
            lines += ["=" * 78, "NOT CAPTURED", "=" * 78, "",
                      "An absent screenshot is not a blank page. Each of these",
                      "records why no rendering exists.", ""]
            for shot in missing:
                lines.append(f"  {shot.url}")
                lines.append(f"    {shot.status.value}: {shot.note}")
            lines.append("")

        out = "\n".join(lines)
        if path is not None:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(out)
        return out

    def export_json(self, path: Path | str | None = None) -> str:
        doc = {
            "kind": "screenshot_log",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "viewport": f"{self.viewport[0]}x{self.viewport[1]}",
            "full_page": self.full_page,
            "note": "screenshots are derived renderings; the wire captures in "
                    "evidence_manifest.json are the primary artifacts",
            "screenshots": [s.to_dict() for s in self.shots],
        }
        out = json.dumps(doc, indent=2)
        if path is not None:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(out)
        return out

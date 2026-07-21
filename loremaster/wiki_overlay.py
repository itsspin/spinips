"""Safe, non-blocking EQL Wiki lookups for Spin's Loremaster.

This module deliberately has no EverQuest process hooks.  Item names arrive
from plain clipboard text, an EverQuest item-link copied as text, a wiki URL,
the opt-in on-demand screen OCR service, or the overlay's search field.
Network work is performed by a single daemon worker and the caller polls a
result queue from Tk's UI thread.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import queue
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse
from urllib.request import Request, urlopen


EQLWIKI_ORIGIN = "https://eqlwiki.com"
USER_AGENT = (
    "Spins-Loremaster/2.0 (EverQuest Legends companion; "
    "https://github.com/itsspin/spinips)"
)
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
LOOKUP_REQUEST_BUDGET_SECONDS = 8.0
DISPLAY_SECTIONS = (
    "Drops From",
    "Sold by",
    "Related quests",
    "Player crafted",
    "Tradeskill recipes",
)
EMPTY_SECTION_TEXT = {
    "Drops From": "This item is not listed as a creature drop.",
    "Sold by": "This item cannot be purchased from merchants.",
    "Related quests": "This item has no related quests.",
    "Player crafted": "This item is not crafted by players.",
    "Tradeskill recipes": "This item is not used in player tradeskills.",
}
PARAMETER_SECTIONS = {
    "dropsfrom": "Drops From",
    "soldby": "Sold by",
    "relatedquests": "Related quests",
    "quests": "Related quests",
    "playercrafted": "Player crafted",
    "tradeskillrecipes": "Tradeskill recipes",
    "recipes": "Tradeskill recipes",
}
EXTRA_PROFILE_LABELS = {
    "merchantvalue": "Merchant value",
    "focuseffect": "Focus Effect",
    "worneffect": "Worn Effect",
    "clickeffect": "Click Effect",
    "proceffect": "Proc Effect",
}


class WikiError(Exception):
    """Base class for user-facing wiki failures."""


class WikiOfflineError(WikiError):
    """No cached result was available while network access was unavailable."""


class WikiNotFoundError(WikiError):
    def __init__(self, query: str, suggestions: list[str] | None = None):
        self.query = query
        self.suggestions = suggestions or []
        super().__init__(f'No exact EQL Wiki item page was found for "{query}".')


@dataclass
class WikiItem:
    title: str
    url: str
    stats: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    sections: dict[str, list[str]] = field(default_factory=dict)
    fetched_at: float = 0.0
    requested_name: str = ""
    cached: bool = False
    stale: bool = False

    @property
    def age_seconds(self) -> float:
        return max(0.0, time.time() - self.fetched_at)


@dataclass
class WikiLookupResult:
    request_id: int
    query: str
    item: WikiItem | None = None
    error: Exception | None = None


def _strip_controls(value: str) -> str:
    return "".join(ch if ch in "\n\t" or ord(ch) >= 32 else " " for ch in value)


def normalize_item_name(value: str) -> str:
    """Turn a user/EQ/wiki string into a conservative item-page title."""
    value = html.unescape(unquote(_strip_controls(value or ""))).strip()
    value = value.replace("_", " ")
    value = re.sub(r"^\s*(?:item|wiki)\s*:\s*", "", value, flags=re.I)
    value = value.strip(" \t\r\n\"'[]<>")
    value = re.sub(r"\s+", " ", value)
    # Legends displays upgraded item ranks as `Item Name +4`, while the wiki
    # uses the base item page for source/location information.
    value = re.sub(r"\s+\+\d+\s*$", "", value)
    return value[:120].strip()


def extract_item_query(value: str) -> tuple[str | None, str]:
    """Extract an item name and provenance from clipboard text.

    Returns ``(None, reason)`` when the clipboard is not safely item-like.
    EQ's native DirectX tooltip is not a Win32 text control, so this function
    never pretends to read the hovered tooltip directly.
    """
    raw = value or ""
    stripped = raw.strip()
    if not stripped:
        return None, "clipboard empty"

    # Canonical EQL Wiki URL, including MediaWiki `title=` links.
    url_match = re.search(r"https?://(?:www\.)?eqlwiki\.com/[^\s<>]+", stripped, re.I)
    if url_match:
        parsed = urlparse(url_match.group(0).rstrip(".,);]"))
        title = parse_qs(parsed.query).get("title", [""])[0]
        if not title:
            title = parsed.path.rsplit("/", 1)[-1]
        title = normalize_item_name(title)
        return (title, "wiki URL") if title else (None, "wiki URL has no title")

    # EQ item links are bracketed by DC2 (0x12).  Builds have used multiple
    # metadata lengths, so prefer a bracketed/display name and otherwise take
    # the final human-readable run rather than depending on a magic offset.
    if "\x12" in raw:
        chunks = [chunk for chunk in raw.split("\x12") if chunk]
        for chunk in reversed(chunks):
            bracketed = re.findall(r"\[([^\[\]\r\n]{2,120})\]", chunk)
            if bracketed:
                name = normalize_item_name(bracketed[-1])
                if _is_item_like(name):
                    return name, "EQ item link"
            runs = re.findall(r"[A-Za-z][A-Za-z0-9'`+,.:()\- ]{2,119}", chunk)
            for run in reversed(runs):
                # Strip leading hexadecimal metadata that sometimes touches
                # the rendered name, without altering ordinary item digits.
                run = re.sub(r"^[0-9A-Fa-f]{24,}", "", run).strip()
                name = normalize_item_name(run)
                if _is_item_like(name):
                    return name, "EQ item link"
        return None, "EQ item link has no readable name"

    bracketed = re.fullmatch(r"\s*\[([^\[\]\r\n]{2,120})\]\s*", raw)
    if bracketed:
        name = normalize_item_name(bracketed.group(1))
        return (name, "bracketed item") if _is_item_like(name) else (None, "not item-like")

    name = normalize_item_name(stripped)
    if _is_item_like(name) and "\n" not in stripped and "\r" not in stripped:
        return name, "clipboard text"
    return None, "clipboard does not contain an item name"


def extract_structured_item_query(value: str) -> tuple[str | None, str]:
    """Read only intentional clipboard structures, never arbitrary text."""
    raw = value or ""
    if "\x12" in raw or re.search(r"https?://(?:www\.)?eqlwiki\.com/", raw, re.I):
        return extract_item_query(raw)
    if re.fullmatch(r"\s*\[[^\[\]\r\n]{2,120}\]\s*", raw):
        return extract_item_query(raw)
    return None, "clipboard has no structured item link"


def clipboard_lookup_plan(value: str) -> tuple[str | None, str, bool]:
    """Return query/source and whether it is safe to submit automatically.

    Arbitrary plaintext can prefill the focused search box, but only an EQ
    link, bracketed item, or EQL Wiki URL is intentional enough to transmit
    without the user confirming it with Enter/click.
    """
    query, source = extract_structured_item_query(value)
    if query:
        return query, source, True
    query, source = extract_item_query(value)
    return query, source, False


def hotkey_lookup_plan(value: str, *, eq_foreground: bool,
                       hover_scan_enabled: bool) -> tuple[str, str | None, str, bool]:
    """Choose the hotkey path without letting stale clipboard data win.

    While EverQuest is foreground, the item under the cursor is the user's
    current intent. A structured clipboard item remains available as a
    fallback if the one-shot hover scan cannot identify a title. Outside EQ,
    intentional links may still open immediately and ordinary text only
    prefills the Lore Lens search field.
    """
    query, source, auto_lookup = clipboard_lookup_plan(value)
    if eq_foreground and hover_scan_enabled:
        return "hover", query, source, auto_lookup
    if query and auto_lookup:
        return "clipboard", query, source, auto_lookup
    return "prompt", query, source, auto_lookup


def _is_item_like(value: str) -> bool:
    if not 2 <= len(value) <= 120 or not re.search(r"[A-Za-z]", value):
        return False
    if value.count(" ") > 15:
        return False
    return not bool(re.search(r"[{}\\/|]", value))


def parse_hotkey(value: str) -> tuple[int, int, str]:
    """Return ``(Win32 modifiers, virtual-key, canonical label)``.

    Supported keys are A-Z, 0-9, and F1-F24.  At least one modifier is
    required so a normal typing key can never be swallowed globally.
    """
    aliases = {
        "ALT": (0x0001, "Alt"),
        "CTRL": (0x0002, "Ctrl"),
        "CONTROL": (0x0002, "Ctrl"),
        "SHIFT": (0x0004, "Shift"),
        "WIN": (0x0008, "Win"),
        "WINDOWS": (0x0008, "Win"),
    }
    pieces = [piece.strip() for piece in re.split(r"\s*\+\s*", value or "") if piece.strip()]
    if len(pieces) < 2:
        raise ValueError("Use at least one modifier, for example Ctrl+Shift+E.")
    mods = 0
    labels: list[str] = []
    key_name = pieces[-1].upper()
    for token in pieces[:-1]:
        entry = aliases.get(token.upper())
        if entry is None:
            raise ValueError(f"Unknown modifier: {token}")
        if not mods & entry[0]:
            mods |= entry[0]
            labels.append(entry[1])
    if re.fullmatch(r"[A-Z0-9]", key_name):
        vk = ord(key_name)
    else:
        fn = re.fullmatch(r"F(\d{1,2})", key_name)
        if not fn or not 1 <= int(fn.group(1)) <= 24:
            raise ValueError("Key must be A-Z, 0-9, or F1-F24.")
        vk = 0x70 + int(fn.group(1)) - 1
    canonical = "+".join(labels + [key_name])
    return mods | 0x4000, vk, canonical  # MOD_NOREPEAT


def _clean_wikitext(value: str, limit: int = 4000) -> list[str]:
    """Convert a small trusted-shape MediaWiki field to bounded plain text."""
    value = _strip_controls(value or "")
    value = re.sub(r"<(?:script|style)\b[^>]*>.*?</(?:script|style)\s*>", "", value,
                   flags=re.I | re.S)
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    value = re.sub(r"<!--.*?-->", "", value, flags=re.S)
    value = re.sub(r"\[\[(?:[^\]|]+\|)?([^\]]+)\]\]", r"\1", value)
    value = re.sub(r"\[(?:https?://\S+)\s+([^\]]+)\]", r"\1", value)
    value = re.sub(r"\[(?:https?://[^\]]+)\]", "", value)
    # Small icon/category/formatting templates add noise and can contain
    # nested pipes.  Iterate to remove innermost templates safely.
    previous = None
    while previous != value:
        previous = value
        value = re.sub(r"\{\{[^{}]*\}\}", "", value)
    value = re.sub(r"<[^>]+>", "", value)
    value = value.replace("'''", "").replace("''", "")
    value = html.unescape(value)
    rows: list[str] = []
    consumed = 0
    for raw_line in value.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        depth = 0
        marker = re.match(r"^(?P<marks>[*#:;]+)\s*(?P<text>.*)$", line)
        if marker:
            marks = marker.group("marks")
            line = marker.group("text").strip()
            depth = max(1, len(marks.replace(":", "")))
        line = re.sub(r"\s+", " ", line).strip(" -\t")
        if not line:
            continue
        if depth:
            line = "  " * (depth - 1) + "• " + line
        remaining = max(0, limit - consumed)
        if remaining <= 0:
            break
        line = line[:remaining]
        rows.append(line)
        consumed += len(line)
    return rows


def _template_parameters(wikitext: str) -> dict[str, str]:
    """Read line-oriented Itempage parameters without splitting nested pipes."""
    params: dict[str, list[str]] = {}
    current: str | None = None
    in_item = False
    for line in (wikitext or "").splitlines():
        if not in_item:
            if re.search(r"\{\{\s*Itempage\b", line, flags=re.I):
                in_item = True
                line = re.split(r"\{\{\s*Itempage\b", line, flags=re.I, maxsplit=1)[1]
            else:
                continue
        if re.match(r"^\s*\}\}\s*(?:</onlyinclude>)?\s*$", line, flags=re.I):
            break
        match = re.match(r"^\s*\|\s*([A-Za-z0-9_ ]+)\s*=\s*(.*)$", line)
        if match:
            current = re.sub(r"[^a-z0-9]", "", match.group(1).casefold())
            params[current] = [match.group(2)]
        elif current is not None:
            params[current].append(line)
    return {key: "\n".join(lines).strip() for key, lines in params.items()}


def parse_item_payload(payload: bytes | str | dict, requested_name: str = "") -> WikiItem:
    """Parse a MediaWiki ``action=parse&prop=wikitext`` response."""
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8", errors="replace")
    if isinstance(payload, str):
        payload = json.loads(payload)
    parsed = payload.get("parse") if isinstance(payload, dict) else None
    if not isinstance(parsed, dict):
        error = payload.get("error", {}) if isinstance(payload, dict) else {}
        raise WikiNotFoundError(requested_name or str(error.get("info", "item")))
    title = normalize_item_name(str(parsed.get("title") or requested_name))
    raw_wikitext = parsed.get("wikitext", "")
    if isinstance(raw_wikitext, dict):
        raw_wikitext = raw_wikitext.get("*", "")
    params = _template_parameters(str(raw_wikitext))
    if not params or not (params.get("itemname") or params.get("statsblock")):
        raise WikiNotFoundError(requested_name or title)
    title = normalize_item_name(params.get("itemname") or title)
    if not title:
        raise WikiNotFoundError(requested_name)
    sections = {name: [] for name in DISPLAY_SECTIONS}
    for parameter, section in PARAMETER_SECTIONS.items():
        if params.get(parameter):
            sections[section].extend(_clean_wikitext(params[parameter]))
    stats = _clean_wikitext(params.get("statsblock", ""), limit=2600)
    structural = {"itemname", "lucyimgid", "statsblock", "notes"}
    structural.update(PARAMETER_SECTIONS)
    for key, value in params.items():
        if key in structural or not value.strip():
            continue
        rows = _clean_wikitext(value, limit=800)
        if not rows:
            continue
        label = EXTRA_PROFILE_LABELS.get(key, key.replace("_", " ").title())
        stats.append(f"{label}: {rows[0]}")
        stats.extend("  " + row for row in rows[1:])
        if len(stats) >= 40:
            break
    slug = quote(title.replace(" ", "_"), safe=":_()-'")
    item = WikiItem(
        title=title,
        requested_name=normalize_item_name(requested_name),
        url=f"{EQLWIKI_ORIGIN}/{slug}",
        stats=stats[:40],
        notes=_clean_wikitext(params.get("notes", ""), limit=1200),
        sections=sections,
        fetched_at=time.time(),
    )
    return item


class WikiCache:
    def __init__(self, directory: Path, ttl_seconds: float, clock: Callable[[], float] = time.time):
        self.directory = Path(directory)
        self.ttl_seconds = max(0.0, float(ttl_seconds))
        self.clock = clock

    @staticmethod
    def _key(name: str) -> str:
        normalized = normalize_item_name(name).casefold().encode("utf-8")
        return hashlib.sha256(normalized).hexdigest()[:32]

    def _path(self, name: str) -> Path:
        return self.directory / f"{self._key(name)}.json"

    def get(self, name: str, allow_stale: bool = False) -> WikiItem | None:
        try:
            raw = json.loads(self._path(name).read_text(encoding="utf-8"))
            item = WikiItem(**raw)
        except (OSError, ValueError, TypeError):
            return None
        age = max(0.0, self.clock() - float(item.fetched_at))
        if age > self.ttl_seconds and not allow_stale:
            return None
        item.cached = True
        item.stale = age > self.ttl_seconds
        return item

    def put(self, requested_name: str, item: WikiItem) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        stored = WikiItem(**asdict(item))
        stored.cached = False
        stored.stale = False
        staged = self._path(requested_name).with_suffix(".json.tmp")
        target = self._path(requested_name)
        alias_staged: Path | None = None
        try:
            staged.write_text(json.dumps(asdict(stored), ensure_ascii=False), encoding="utf-8")
            os.replace(staged, target)
            if item.title.casefold() != normalize_item_name(requested_name).casefold():
                alias = self._path(item.title)
                alias_staged = alias.with_suffix(".json.tmp")
                alias_staged.write_text(json.dumps(asdict(stored), ensure_ascii=False), encoding="utf-8")
                os.replace(alias_staged, alias)
        finally:
            staged.unlink(missing_ok=True)
            if alias_staged is not None:
                alias_staged.unlink(missing_ok=True)


class WikiClient:
    """Rate-limited EQL Wiki client with fresh and stale disk caching."""

    def __init__(self, cache: WikiCache, timeout: float = 6.0,
                 opener: Callable = urlopen, network_enabled: bool = True,
                 min_request_interval: float = 0.35,
                 monotonic: Callable[[], float] = time.monotonic,
                 sleeper: Callable[[float], None] = time.sleep):
        self.cache = cache
        self.timeout = max(1.0, min(float(timeout), 20.0))
        self.opener = opener
        self.network_enabled = bool(network_enabled)
        self.min_request_interval = max(0.0, float(min_request_interval))
        self.monotonic = monotonic
        self.sleeper = sleeper
        self._last_request = 0.0

    def _deadline_timeout(self, deadline: float | None) -> float:
        if deadline is None:
            return self.timeout
        remaining = deadline - self.monotonic()
        if remaining <= 0:
            raise WikiOfflineError("EQL Wiki lookup exceeded its request budget.")
        return min(self.timeout, remaining)

    def _json_request(self, params: dict, *, deadline: float | None = None) -> dict:
        delay = self.min_request_interval - (self.monotonic() - self._last_request)
        if delay > 0:
            if deadline is not None and delay >= deadline - self.monotonic():
                raise WikiOfflineError("EQL Wiki lookup exceeded its request budget.")
            self.sleeper(delay)
        request_timeout = self._deadline_timeout(deadline)
        url = f"{EQLWIKI_ORIGIN}/api.php?{urlencode(params)}"
        request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        self._last_request = self.monotonic()
        try:
            response = self.opener(request, timeout=request_timeout)
            with response:
                data = response.read(MAX_RESPONSE_BYTES + 1)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise WikiOfflineError("EQL Wiki is unavailable; no fresh response was received.") from exc
        if len(data) > MAX_RESPONSE_BYTES:
            raise WikiError("EQL Wiki returned an unexpectedly large response.")
        try:
            return json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise WikiError("EQL Wiki returned an unreadable response.") from exc

    def _suggestions(self, query: str, *, deadline: float | None = None) -> list[str]:
        try:
            payload = self._json_request({
                "action": "query", "format": "json", "generator": "search",
                "gsrsearch": query, "gsrlimit": 5, "gsrnamespace": 0,
                "prop": "info", "redirects": 1,
            }, deadline=deadline)
        except WikiError:
            return []
        pages = payload.get("query", {}).get("pages", {})
        values = pages.values() if isinstance(pages, dict) else pages
        return [normalize_item_name(str(page.get("title", "")))
                for page in values if isinstance(page, dict) and page.get("title")][:5]

    def fetch(self, query: str, *, include_suggestions: bool = True,
              deadline: float | None = None) -> WikiItem:
        query = normalize_item_name(query)
        if not _is_item_like(query):
            raise WikiNotFoundError(query or "item")
        fresh = self.cache.get(query)
        if fresh is not None:
            return fresh
        stale = self.cache.get(query, allow_stale=True)
        if not self.network_enabled:
            if stale is not None:
                stale.stale = True
                return stale
            raise WikiOfflineError("Wiki network lookups are disabled and this item is not cached.")
        try:
            payload = self._json_request({
                "action": "parse", "format": "json", "page": query,
                "prop": "wikitext|sections", "redirects": 1,
            }, deadline=deadline)
            if "parse" not in payload:
                suggestions = self._suggestions(
                    query, deadline=deadline) if include_suggestions else []
                raise WikiNotFoundError(query, suggestions)
            item = parse_item_payload(payload, requested_name=query)
            item.fetched_at = time.time()
            try:
                self.cache.put(query, item)
            except OSError:
                pass  # a read-only cache must never hide a successful lookup
            return item
        except WikiNotFoundError:
            raise
        except WikiError:
            if stale is not None:
                stale.stale = True
                return stale
            raise


class WikiLookupService:
    """One background worker; UI code polls results without touching Tk off-thread."""

    def __init__(self, client: WikiClient):
        self.client = client
        self.requests: queue.Queue[tuple[int, tuple[str, ...]] | None] = queue.Queue(maxsize=4)
        self.results: queue.Queue[WikiLookupResult] = queue.Queue()
        self._request_id = 0
        self._generation_lock = threading.Lock()
        self._closed = threading.Event()
        self._thread = threading.Thread(target=self._run, name="LoremasterWiki", daemon=True)
        self._thread.start()

    def submit(self, query: str) -> int:
        return self.submit_candidates([query])

    def submit_candidates(self, candidates: Iterable[str]) -> int:
        with self._generation_lock:
            self._request_id += 1
            request_id = self._request_id
        normalized: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            item_name = normalize_item_name(candidate)
            key = item_name.casefold()
            if item_name and key not in seen:
                normalized.append(item_name)
                seen.add(key)
            if len(normalized) >= 4:
                break
        if not normalized:
            normalized.append("")
        # Keep the latest intent when a user types several searches quickly.
        while True:
            try:
                self.requests.get_nowait()
            except queue.Empty:
                break
        try:
            self.requests.put_nowait((request_id, tuple(normalized)))
        except queue.Full:
            pass
        return request_id

    def poll(self) -> list[WikiLookupResult]:
        found = []
        with self._generation_lock:
            latest_request_id = self._request_id
            while True:
                try:
                    result = self.results.get_nowait()
                except queue.Empty:
                    return found
                if result.request_id == latest_request_id:
                    found.append(result)

    def close(self) -> None:
        self._closed.set()
        while True:
            try:
                self.requests.get_nowait()
            except queue.Empty:
                break
        self.requests.put_nowait(None)

    def _is_current(self, request_id: int) -> bool:
        if self._closed.is_set():
            return False
        with self._generation_lock:
            return request_id == self._request_id

    def _run(self) -> None:
        while True:
            request = self.requests.get()  # zero wakeups/CPU while idle
            if request is None or self._closed.is_set():
                return
            request_id, candidates = request
            if not self._is_current(request_id):
                continue
            query = candidates[0]
            result = None
            errors: list[Exception] = []
            suggestions: list[str] = []
            stale_fallbacks: list[tuple[str, WikiItem]] = []

            # OCR returns ranked candidates. Resolve every disk-cache hit
            # before starting HTTP so a later, known item wins immediately
            # over an earlier tooltip heading or other OCR noise.
            if isinstance(self.client, WikiClient):
                for candidate in candidates:
                    if not self._is_current(request_id):
                        break
                    cached = self.client.cache.get(candidate)
                    if cached is not None:
                        result = WikiLookupResult(request_id, candidate, item=cached)
                        break
                    stale = self.client.cache.get(candidate, allow_stale=True)
                    if stale is not None:
                        stale_fallbacks.append((candidate, stale))
                if not self._is_current(request_id):
                    continue
                if result is None and not self.client.network_enabled:
                    if stale_fallbacks:
                        candidate, stale = stale_fallbacks[0]
                        stale.stale = True
                        result = WikiLookupResult(request_id, candidate, item=stale)
                    else:
                        errors.append(WikiOfflineError(
                            "Wiki network lookups are disabled and none of the "
                            "hover candidates are cached."))

            deadline = None
            if isinstance(self.client, WikiClient):
                deadline = self.client.monotonic() + LOOKUP_REQUEST_BUDGET_SECONDS

            stale_generation = False
            for index, candidate in enumerate(candidates) if result is None and not errors else ():
                if not self._is_current(request_id):
                    stale_generation = True
                    break
                try:
                    if isinstance(self.client, WikiClient):
                        item = self.client.fetch(
                            candidate,
                            include_suggestions=(index == len(candidates) - 1),
                            deadline=deadline,
                        )
                    else:
                        item = self.client.fetch(candidate)
                    if not self._is_current(request_id):
                        stale_generation = True
                        break
                    result = WikiLookupResult(request_id, candidate, item=item)
                    break
                except WikiNotFoundError as exc:
                    if not self._is_current(request_id):
                        stale_generation = True
                        break
                    errors.append(exc)
                    suggestions.extend(exc.suggestions)
                except WikiOfflineError as exc:
                    if not self._is_current(request_id):
                        stale_generation = True
                        break
                    errors.append(exc)
                    if stale_fallbacks:
                        candidate, stale = stale_fallbacks[0]
                        stale.stale = True
                        result = WikiLookupResult(request_id, candidate, item=stale)
                    break
                except Exception as exc:  # contained and rendered as a clear overlay state
                    if not self._is_current(request_id):
                        stale_generation = True
                        break
                    errors.append(exc)
                    break
            if stale_generation or not self._is_current(request_id):
                continue
            if result is None:
                error = errors[-1] if errors else WikiNotFoundError(query)
                if errors and all(isinstance(exc, WikiNotFoundError) for exc in errors):
                    error = WikiNotFoundError(query, list(dict.fromkeys(suggestions))[:5])
                result = WikiLookupResult(request_id, query, error=error)
            if self._is_current(request_id):
                self.results.put(result)


def format_cache_age(item: WikiItem, now: float | None = None) -> str:
    age = max(0.0, (time.time() if now is None else now) - item.fetched_at)
    if age < 60:
        return "just now"
    if age < 3600:
        return f"{int(age // 60)}m ago"
    if age < 86400:
        return f"{int(age // 3600)}h ago"
    return f"{int(age // 86400)}d ago"


def selftest() -> None:
    """Deterministic backend coverage used by ``loremaster.py --selftest``."""
    import tempfile

    payload = json.dumps({
        "parse": {
            "title": "Cloak of Flames",
            "wikitext": {"*": (
                "{{Classic Era}}\n<onlyinclude>{{Itempage\n"
                "|itemname = Cloak of Flames\n"
                "|statsblock = MAGIC ITEM<br>\nSlot: BACK<br>\nHaste: +36%<br>\n"
                "|notes = '''A relic''' <script>bad()</script>\n"
                "|dropsfrom = [[Nagafen's Lair]]\n* [[Lord Nagafen]]\n"
                "}}</onlyinclude>"
            )},
        }
    }).encode("utf-8")

    parsed = parse_item_payload(payload, "Cloak of Flames +4")
    assert parsed.title == "Cloak of Flames"
    assert "Haste: +36%" in parsed.stats
    assert parsed.sections["Drops From"] == ["Nagafen's Lair", "• Lord Nagafen"]
    assert all("<" not in line and "script" not in line for line in parsed.notes)
    assert extract_item_query("https://eqlwiki.com/Cloak_of_Flames")[0] == parsed.title
    assert extract_item_query("[Cloak of Flames +4]")[0] == parsed.title
    assert parse_hotkey("Ctrl+Shift+E") == (0x4006, ord("E"), "Ctrl+Shift+E")

    class Response:
        def __init__(self, data):
            self.data = data

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit=-1):
            return self.data

    with tempfile.TemporaryDirectory() as temp_dir:
        calls = []

        def exact(request, timeout):
            calls.append((request.full_url, timeout))
            return Response(payload)

        cache = WikiCache(Path(temp_dir), 3600)
        client = WikiClient(cache, opener=exact, min_request_interval=0)
        first = client.fetch("Cloak of Flames")
        second = client.fetch("Cloak of Flames")
        assert first.title == second.title and second.cached and len(calls) == 1

        # Stale cache survives offline mode without blocking on any network.
        cached_path = cache._path("Cloak of Flames")
        raw = json.loads(cached_path.read_text(encoding="utf-8"))
        raw["fetched_at"] = 1.0
        cached_path.write_text(json.dumps(raw), encoding="utf-8")
        cache.ttl_seconds = 0
        offline = WikiClient(cache, network_enabled=False, min_request_interval=0)
        assert offline.fetch("Cloak of Flames").stale

    with tempfile.TemporaryDirectory() as temp_dir:
        responses = [
            json.dumps({"error": {"code": "missingtitle"}}).encode(),
            json.dumps({"query": {"pages": {
                "12": {"title": "Cloak of Flames"},
                "13": {"title": "Cloak of Crystalline Waters"},
            }}}).encode(),
        ]

        def fuzzy(_request, timeout):
            return Response(responses.pop(0))

        client = WikiClient(WikiCache(Path(temp_dir), 0), opener=fuzzy,
                            min_request_interval=0)
        try:
            client.fetch("Clok of Flames")
        except WikiNotFoundError as exc:
            assert exc.suggestions[0] == "Cloak of Flames"
        else:
            raise AssertionError("missing item did not produce fuzzy suggestions")

    with tempfile.TemporaryDirectory() as temp_dir:
        def oversized(_request, timeout):
            return Response(b"x" * (MAX_RESPONSE_BYTES + 1))

        client = WikiClient(WikiCache(Path(temp_dir), 0), opener=oversized,
                            min_request_interval=0)
        try:
            client.fetch("Oversized Item")
        except WikiError as exc:
            assert "large" in str(exc)
        else:
            raise AssertionError("oversized response was accepted")

    # Submitting is constant-time; the worker owns all fetch latency.
    gate = threading.Event()

    class SlowClient:
        def fetch(self, query):
            gate.wait(0.5)
            return parsed

    service = WikiLookupService(SlowClient())
    started = time.monotonic()
    request_id = service.submit("Cloak of Flames")
    assert time.monotonic() - started < 0.05
    gate.set()
    deadline = time.monotonic() + 1.0
    results = []
    while time.monotonic() < deadline and not results:
        results = service.poll()
        time.sleep(0.005)
    service.close()
    assert results and results[0].request_id == request_id and results[0].item.title == parsed.title

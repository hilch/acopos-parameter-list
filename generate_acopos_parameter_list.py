"""Download ACOPOS parameters and build a self-contained searchable HTML file."""
from __future__ import annotations

import argparse
import hashlib
import html as html_lib
import json
import logging
import re
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from posixpath import normpath
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup, NavigableString, Tag

LOG = logging.getLogger("acopos")
API_URL = "https://help.br-automation.com/api/v1/content/path"
HELP_ROOT = "https://help.br-automation.com/"
PARAM_PREFIX = "ncsoftware/acp10_parameter/html/"
LOCAL_HELP_PREFIXES = ("ncsoftware/acp10_drivefunctions/", "ncsoftware/acp10_errortext/")
HELP_ROOT_PATHS = ("ncsoftware/acp10_drivefunctions/acopos_index.html", "ncsoftware/acp10_errortext/index.htm")
ALLOWED_TAGS = {"p", "br", "b", "strong", "i", "em", "u", "sub", "sup", "ul", "ol", "li", "table", "thead", "tbody", "tr", "th", "td", "a", "span", "div", "code", "pre", "h2", "h3", "h4", "img"}
ALLOWED_ATTRS = {"href", "title", "class", "colspan", "rowspan", "src", "alt"}
FIELD_LABELS = re.compile(r"\s*:\s*$")
NC_TOKEN = re.compile(r"\bnc[A-Z0-9_]+\b")
HELP_ROUTE = re.compile(r"#/[^/]+/[^/]+/(?P<path>[^?#]+)")


@dataclass
class NcConstant:
    """Represent an NC constant parsed from the help documentation."""

    name: str
    datatype: str
    raw_value: str
    decimal: int | None
    description: str


@dataclass
class ParameterRef:
    """Identify a parameter and the source page containing its details."""

    id: int
    datatype: str
    define: str
    title: str
    source_path: str


@dataclass
class Parameter:
    """Store parsed parameter metadata, content, and missing-page status."""

    id: int
    define: str
    title: str
    datatype: str
    access: str
    source_path: str
    fields: dict[str, str] = field(default_factory=dict)
    description_html: str = ""
    search_text: str = ""
    missing: bool = False


class HelpClient:
    """Fetch help pages and assets with optional response caching and retries."""

    def __init__(self, lang: str, version: str, cache_dir: Path, use_cache: bool, refresh: bool, delay: float, workers: int = 8) -> None:
        """Initialize language, cache, throttling, and worker settings.

        Args:
            lang: Language code requested from the help API.
            version: Help documentation version requested from the API.
            cache_dir: Directory for cached API responses.
            use_cache: Whether API responses may be read from or written to cache.
            refresh: Whether to bypass cached reads while replacing cache entries.
            delay: Minimum interval between API requests, in seconds.
            workers: Maximum number of concurrent download workers.

        Returns:
            None.
        """
        self.lang, self.version = lang, version
        self.cache_dir = cache_dir
        self.use_cache, self.refresh, self.delay, self.workers = use_cache, refresh, delay, max(1, workers)
        self.session = requests.Session()
        self.lock = threading.Lock()
        self.next_slot = 0.0

    def _cache_path(self, path: str) -> Path:
        """Build the cache filename for a language, version, and help path.

        Args:
            path: Help content path used in the API request.

        Returns:
            Path to the corresponding JSON cache file.
        """
        cache_key = f"{self.lang}/{self.version}/{path}"
        return self.cache_dir / (hashlib.sha1(cache_key.encode("utf-8")).hexdigest() + ".json")

    def get(self, path: str) -> str:
        """Return a help page from cache or the content API.

        Args:
            path: Help content path to retrieve.

        Returns:
            The page's HTML content.

        Raises:
            RuntimeError: If the API request fails after all retries.
        """
        cache_path = self._cache_path(path)
        # A refresh bypasses reads but still writes the newly downloaded response.
        if self.use_cache and not self.refresh and cache_path.exists():
            try:
                return json.loads(cache_path.read_text(encoding="utf-8"))["html"]
            except (OSError, KeyError, json.JSONDecodeError) as exc:
                LOG.warning("Invalid cache for %s: %s", path, exc)
        last_error: Exception | None = None
        for attempt in range(3):
            # Share one request schedule across workers to avoid bursts against the help API.
            with self.lock:
                wait = self.next_slot - time.monotonic()
                if wait > 0:
                    time.sleep(wait)
                self.next_slot = time.monotonic() + self.delay
            try:
                response = self.session.put(API_URL, json={"id": "", "lang": self.lang, "version": self.version, "path": path}, timeout=30)
                if response.status_code >= 500:
                    raise requests.HTTPError(f"HTTP {response.status_code}")
                response.raise_for_status()
                payload = response.json()
                if payload.get("error"):
                    raise RuntimeError(str(payload["error"]))
                content = str(payload.get("html", ""))
                if self.use_cache:
                    self.cache_dir.mkdir(parents=True, exist_ok=True)
                    cache_path.write_text(json.dumps({"html": content}, ensure_ascii=False), encoding="utf-8")
                return content
            except (requests.RequestException, ValueError, RuntimeError) as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(0.5 * (2**attempt))
                    LOG.debug("Retry %s (%d/3): %s", path, attempt + 2, exc)
        raise RuntimeError(f"Loading failed: {path}: {last_error}")

    def get_asset(self, url: str) -> bytes:
        """Download an asset from its URL.

        Args:
            url: Absolute URL of the asset to download.

        Returns:
            The asset's response body as bytes.

        Raises:
            RuntimeError: If the download fails after all retries.
        """
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = self.session.get(url, timeout=30)
                if response.status_code >= 500:
                    raise requests.HTTPError(f"HTTP {response.status_code}")
                response.raise_for_status()
                return response.content
            except requests.RequestException as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(0.5 * (2**attempt))
        raise RuntimeError(f"Asset loading failed: {url}: {last_error}")


def text_of(node: Tag) -> str:
    """Return a tag's visible text with whitespace normalized.

    Args:
        node: BeautifulSoup tag whose text should be extracted.

    Returns:
        Visible text with runs of whitespace replaced by single spaces.
    """
    return " ".join(node.get_text(" ", strip=True).split())


def plain_html_text(value: str) -> str:
    """Extract plain text from an HTML fragment.

    Args:
        value: HTML fragment to parse.

    Returns:
        Text content with surrounding and repeated whitespace removed.
    """
    return BeautifulSoup(value, "lxml").get_text(" ", strip=True)


def base_datatype(value: str) -> str:
    """Remove any parenthesized suffix from a data type.

    Args:
        value: Data type label, optionally followed by parenthesized details.

    Returns:
        The base data type without the parenthesized suffix.
    """
    return value.split("(", 1)[0].strip()


def help_path_from_href(href: str) -> str | None:
    """Extract the content path from a localized Automation Help URL.

    Args:
        href: Link containing an Automation Help language/version route.

    Returns:
        The URL-decoded content path, or None if the route is not recognized.
    """
    match = HELP_ROUTE.search(href)
    return unquote(match["path"]) if match else None


def parse_constants(source: str) -> dict[str, NcConstant]:
    """Parse numeric NC constant definitions from an HTML page.

    Args:
        source: HTML source containing the constants table.

    Returns:
        Constants indexed by their NC names; the first conflicting definition is kept.
    """
    soup = BeautifulSoup(source, "lxml")
    constants: dict[str, NcConstant] = {}
    for row in soup.select("tr"):
        cells = row.find_all("td", recursive=False)
        if len(cells) < 4:
            continue
        name = text_of(cells[0])
        value = text_of(cells[2])
        if not name.startswith("nc") or not re.fullmatch(r"(?:0x[0-9a-fA-F]+|-?\d+)", value):
            continue
        decimal = int(value, 16) if value.lower().startswith("0x") else int(value)
        constant = NcConstant(name, text_of(cells[1]), value, decimal, text_of(cells[3]))
        old = constants.get(name)
        if old and old.raw_value != value:
            LOG.warning("Constant %s duplicated with a different value; keeping the first definition", name)
        else:
            constants.setdefault(name, constant)
    return constants


def parse_overview(source: str) -> list[ParameterRef]:
    """Parse parameter identifiers and detail-page paths from an overview.

    Args:
        source: HTML source containing the parameter overview table.

    Returns:
        Parameter references in source order. Unrecognized row labels have an ID of -1.
    """
    soup = BeautifulSoup(source, "lxml")
    result: list[ParameterRef] = []
    pattern = re.compile(r"^\s*(?P<id>\d+)\s*-\s*(?P<datatype>.+?)\s*-\s*(?P<define>\w+)\s*:\s*(?P<title>.+?)\s*$")
    for link in soup.select("table.nolines_tab a[href]"):
        raw = text_of(link)
        match = pattern.match(raw)
        href = link.get("href", "")
        path = help_path_from_href(href) or href.lstrip("#/")
        if not path.startswith(PARAM_PREFIX):
            continue
        if match:
            result.append(ParameterRef(int(match["id"]), base_datatype(match["datatype"]), match["define"], match["title"], path))
        else:
            LOG.warning("Overview row not recognized: %s", raw)
            result.append(ParameterRef(-1, "", "", raw, path))
    return result


def parse_parameter(source: str, ref: ParameterRef) -> Parameter:
    """Parse metadata, fields, and description from one parameter page.

    Args:
        source: HTML source for the parameter detail page.
        ref: Overview reference supplying fallback metadata and the source path.

    Returns:
        A parameter populated with parsed values and HTML content.
    """
    soup = BeautifulSoup(source, "lxml")
    fields: dict[str, str] = {}
    description = ""
    for row in soup.select("table tr"):
        cells = row.find_all("td", recursive=False)
        if len(cells) < 2:
            continue
        label = FIELD_LABELS.sub("", text_of(cells[0])).strip()
        if label:
            fields[label] = "".join(str(x) for x in cells[1].contents).strip()
    # Source pages do not consistently represent descriptions as labeled table rows.
    for label in soup.find_all(string=lambda value: value and value.strip().rstrip(":").casefold() in {"description", "beschreibung"}):
        parent = label.parent
        if parent:
            description = "".join(str(x) for x in parent.parent.contents if x is not label.parent).strip() if parent.parent else ""
            if description:
                break
    plain_fields = {
        label: BeautifulSoup(value, "lxml").get_text(" ", strip=True)
        for label, value in fields.items()
    }
    datatype = base_datatype(plain_fields.get("Data type", ref.datatype))
    access = plain_fields.get("Access", "")
    define = plain_fields.get("Define", ref.define)
    return Parameter(ref.id, define, ref.title, datatype, access, ref.source_path, fields, description)


def local_help_href(path: str, local_help: Path | None, source_file: Path | None) -> str | None:
    """Build a relative link to a page in the downloaded local-help tree.

    Args:
        path: Help content path to link to.
        local_help: Root directory of the local help tree, if available.
        source_file: File containing the link, used to calculate its relative path.

    Returns:
        A relative URL for supported local-help sections, or None otherwise.
    """
    path = unquote(path)
    if not local_help or not any(path.startswith(prefix) for prefix in LOCAL_HELP_PREFIXES):
        return None
    target = local_help / Path(path)
    return Path(__import__("os").path.relpath(target, source_file.parent if source_file else Path.cwd())).as_posix()


def normalize_local_relative_href(href: str) -> str:
    """Convert source-help cross-tree links into local tree paths.

    Args:
        href: Relative link found in a downloaded help page.

    Returns:
        The normalized local tree path when a supported help section is present;
        otherwise, the original link.
    """
    decoded = unquote(href).split("#", 1)[0].split("?", 1)[0]
    for prefix in LOCAL_HELP_PREFIXES:
        marker = prefix.rstrip("/") + "/"
        if marker in decoded:
            return marker + decoded.split(marker, 1)[1]
        short_marker = "../../" + prefix.split("/", 1)[1]
        if decoded.startswith(short_marker):
            return prefix + decoded[len(short_marker):]
    return href


def sanitize_and_enrich(fragment: str, constants: dict[str, NcConstant], param_ids: dict[str, int], local_help: Path | None = None, source_file: Path | None = None) -> str:
    """Sanitize an HTML fragment and enrich its links and NC constants.

    Args:
        fragment: Source HTML fragment to clean and rewrite.
        constants: Known NC constants used to add numeric values.
        param_ids: Parameter page filenames mapped to parameter IDs.
        local_help: Optional root directory for local help links.
        source_file: Optional output file used to calculate relative help links.

    Returns:
        Sanitized HTML with supported links rewritten and known constants enriched.
    """
    soup = BeautifulSoup(fragment or "", "lxml")
    unknown: set[str] = set()
    # Keep supported formatting, unwrap harmless unknown tags, and remove active/embed content.
    for node in list(soup.find_all(True)):
        if node.name not in ALLOWED_TAGS:
            node.unwrap() if node.name not in {"script", "style", "link", "meta", "iframe", "object", "embed", "form"} else node.decompose()
            continue
        for attr in list(node.attrs):
            if attr.lower().startswith("on") or attr not in ALLOWED_ATTRS:
                del node.attrs[attr]
        if node.get("href"):
            href = str(node["href"])
            help_path = help_path_from_href(href)
            if href.lower().startswith("javascript:"):
                del node.attrs["href"]
            elif help_path:
                match = re.search(r"acp10_parameter/html/([^/?#]+)", help_path)
                if match and match.group(1) in param_ids:
                    node["href"] = f"#param-{param_ids[match.group(1)]}"
                elif (local_href := local_help_href(help_path, local_help, source_file)):
                    node["href"] = local_href
                else:
                    node["href"] = urljoin(HELP_ROOT, href)
                    node["target"] = "_blank"
                    node["rel"] = "noopener noreferrer"
            elif href.startswith("https://buronlinehelpprod.blob.core.windows.net"):
                del node.attrs["href"]
        if node.get("src") and not str(node["src"]).startswith("https:"):
            del node.attrs["src"]
    names = sorted(constants, key=len, reverse=True)
    if names:
        token_pattern = re.compile(r"\b(?:" + "|".join(re.escape(n) for n in names) + r")\b")
        for text_node in list(soup.find_all(string=True)):
            if not isinstance(text_node, NavigableString) or text_node.parent.name in {"script", "style"} or "nc-const-value" in text_node.parent.get("class", []):
                continue
            text = str(text_node)
            parts: list[str] = []
            position = 0
            for match in token_pattern.finditer(text):
                parts.append(text[position:match.start()])
                constant = constants[match.group()]
                shown = f" ({constant.raw_value} / {constant.decimal})" if constant.raw_value.lower().startswith("0x") else f" ({constant.decimal})"
                parts.append(f'<span class="nc-const-value" title="{html_lib.escape(constant.description, quote=True)}">{match.group()}{shown}</span>')
                position = match.end()
            if parts:
                parts.append(text[position:])
                replacement = BeautifulSoup("".join(parts), "lxml")
                text_node.replace_with(*replacement.body.contents)
            for unknown_name in NC_TOKEN.findall(text):
                if unknown_name not in constants:
                    unknown.add(unknown_name)
    if unknown:
        LOG.warning("Unknown NC constants: %s", ", ".join(sorted(unknown)))
    body = soup.body
    return "".join(str(x) for x in (body.contents if body else soup.contents))


def download_help_tree(client: HelpClient, output_dir: Path) -> Path:
    """Download the selected help sections, linked pages, and referenced assets.

    Args:
        client: Client configured for the requested language and version.
        output_dir: Destination directory; any existing directory is removed first.

    Returns:
        The output directory containing the downloaded local help tree.
    """
    pages: dict[str, str] = {}
    pending = list(HELP_ROOT_PATHS)
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    assets: dict[str, Path] = {}
    seen: set[str] = set()
    # Follow links within the selected help sections until no unseen pages remain.
    while pending:
        batch = [path for path in pending if path not in seen]
        pending.clear()
        seen.update(batch)
        with ThreadPoolExecutor(max_workers=getattr(client, "workers", 8)) as pool:
            futures = {pool.submit(client.get, path): path for path in batch}
            for future in as_completed(futures):
                path = futures[future]
                try:
                    source = future.result()
                except Exception as exc:
                    LOG.warning("Help page could not be loaded %s: %s", path, exc)
                    continue
                pages[path] = source
                soup = BeautifulSoup(source, "lxml")
                for link in soup.select("a[href]"):
                    href = str(link["href"])
                    linked_path = help_path_from_href(href)
                    if linked_path and any(linked_path.startswith(prefix) for prefix in LOCAL_HELP_PREFIXES) and linked_path not in seen:
                        pending.append(linked_path)
                for element in soup.find_all(["link", "script", "img"]):
                    raw_url = element.get("href") or element.get("src")
                    if not raw_url:
                        continue
                    asset_url = urljoin(HELP_ROOT, str(raw_url))
                    if asset_url.startswith("https://"):
                        asset_path = Path("assets") / Path(unquote(asset_url.split("/", 3)[-1]))
                        assets[asset_url] = asset_path
    for asset_url, relative_path in assets.items():
        destination = output_dir / relative_path
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                destination.write_bytes(client.get_asset(asset_url))
            except Exception as exc:
                LOG.warning("Asset could not be loaded %s: %s", asset_url, exc)
    for path, source in pages.items():
        soup = BeautifulSoup(source, "lxml")
        current_file = output_dir / Path(path)
        for node in soup.select("a[href]"):
            href = str(node["href"])
            normalized = normalize_local_relative_href(href)
            if normalized != href:
                target = output_dir / normalized
                if target.exists():
                    node["href"] = Path(__import__("os").path.relpath(target, current_file.parent)).as_posix()
                else:
                    del node["href"]
            elif (target_path := help_path_from_href(href)):
                local_href = local_help_href(target_path, output_dir, current_file)
                if local_href:
                    suffix = ("#" + href.split("#", 2)[-1]) if href.count("#") > 1 else ""
                    node["href"] = local_href + suffix
                else:
                    node["href"] = urljoin(HELP_ROOT, href)
                    node["target"] = "_blank"
                    node["rel"] = "noopener noreferrer"
            elif href.startswith("javascript:"):
                del node["href"]
        for node in soup.select("link[href], script[src], img[src]"):
            attribute = "href" if node.name == "link" else "src"
            raw_url = str(node.get(attribute, ""))
            if node.name == "script" and raw_url.lower().endswith("iframe-communicator.js"):
                node.decompose()
                continue
            asset_url = urljoin(HELP_ROOT, raw_url)
            if asset_url in assets:
                node[attribute] = Path(__import__("os").path.relpath(output_dir / assets[asset_url], current_file.parent)).as_posix()
        current_file.parent.mkdir(parents=True, exist_ok=True)
        current_file.write_text(str(soup), encoding="utf-8")
    # Remove local links whose page or fragment was not included in the downloaded tree.
    for current_file in [*output_dir.rglob("*.htm"), *output_dir.rglob("*.html")]:
        soup = BeautifulSoup(current_file.read_text(encoding="utf-8"), "lxml")
        changed = False
        anchors = {str(value) for tag in soup.find_all(True) for attribute in ("id", "name") if (value := tag.get(attribute))}
        for node in soup.select("a[href]"):
            href = unquote(str(node["href"]))
            parsed = urlparse(href)
            if parsed.scheme or href.startswith("//"):
                continue
            if href.startswith("#"):
                if href != "#" and href[1:] not in anchors:
                    del node["href"]
                    changed = True
                continue
            if not (current_file.parent / parsed.path).resolve().exists():
                del node["href"]
                changed = True
        if changed:
            current_file.write_text(str(soup), encoding="utf-8")
    LOG.info("Local help saved: %d HTML pages, %d assets under %s", len(pages), len(assets), output_dir)
    return output_dir


def render_html(parameters: list[Parameter], lang: str, version: str) -> str:
    """Render parameter data as a standalone searchable HTML document.

    Args:
        parameters: Parameters to include in the document.
        lang: Language code associated with the source help content.
        version: Help documentation version to display in the document.

    Returns:
        Complete HTML document with embedded styles and search behavior.
    """
    rows = []
    for parameter in parameters:
        search = " ".join([str(parameter.id), parameter.define, parameter.title, parameter.datatype, parameter.access, *(plain_html_text(value) for value in parameter.fields.values()), plain_html_text(parameter.description_html)]).lower()
        fields = "".join(f'<tr><th>{html_lib.escape(label)}</th><td>{value}</td></tr>' for label, value in parameter.fields.items())
        missing = '<p class="missing">Details not available</p>' if parameter.missing else ""
        rows.append(f'<details id="param-{parameter.id}" class="param" data-id="{parameter.id}" data-define="{html_lib.escape(parameter.define, quote=True)}" data-title="{html_lib.escape(parameter.title, quote=True)}" data-datatype="{html_lib.escape(parameter.datatype, quote=True)}" data-access="{html_lib.escape(parameter.access, quote=True)}" data-search="{html_lib.escape(search, quote=True)}"><summary>{html_lib.escape(str(parameter.id))} — {html_lib.escape(parameter.datatype)} — {html_lib.escape(parameter.define)}: {html_lib.escape(parameter.title)}</summary><div class="details">{missing}<table><tbody>{fields}</tbody></table>{parameter.description_html}</div></details>')
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    # Keep CSS and search behavior inline so the output works without a web server.
    css = """*{box-sizing:border-box}body{margin:0;background:#f4f1ea;color:#202a2e;font:16px Georgia,serif}header,main{max-width:1100px;margin:auto;padding:28px 20px}header{border-bottom:3px solid #d96c3b}h1{margin:0 0 8px;font:700 2rem 'Trebuchet MS',sans-serif}small{color:#536269}.tools{display:flex;gap:10px;flex-wrap:wrap;margin:20px 0;position:sticky;top:0;background:#f4f1ea;padding:12px 0;z-index:2}input,select,button{font:inherit;padding:10px;border:1px solid #9da8a8;background:#fff}input{flex:1;min-width:220px}button{cursor:pointer;background:#d96c3b;color:white;border-color:#d96c3b}.param{background:white;border:1px solid #cad1cf;margin:8px 0}.param summary{cursor:pointer;padding:12px;font-family:'Trebuchet MS',sans-serif;font-weight:bold}.details{padding:0 14px 16px}.details table{border-collapse:collapse;width:100%;margin:8px 0}.details th,.details td{border-bottom:1px solid #e1e5e3;padding:7px;text-align:left;vertical-align:top}.details th{width:190px;color:#536269}.nc-const-value{color:#b44724;font-weight:bold}.missing{color:#b44724;font-style:italic}.hidden{display:none}@media(max-width:600px){body{font-size:15px}header,main{padding:20px 12px}.tools>*{width:100%}.details th{width:35%}}"""
    js = """(() => { const input=document.querySelector('#search'), count=document.querySelector('#count'), clear=document.querySelector('#clear'), type=document.querySelector('#type'), access=document.querySelector('#access'), items=[...document.querySelectorAll('.param')]; const run=()=>{const raw=input.value.trim().toLowerCase(), terms=raw.split(/\\s+/).filter(Boolean), exact=/^\\d+$/.test(raw), filtered=items.filter(x=>{const data=x.dataset, words=terms.every(t=>{let key='search', value=data.search;if(t.includes(':')){const p=t.indexOf(':');key={define:'define',id:'id',type:'datatype'}[t.slice(0,p)]||key;value=data[key]||'';t=t.slice(p+1)}return value.includes(t)});return words&&(!type.value||data.datatype===type.value)&&(!access.value||data.access===access.value)});items.forEach(x=>x.classList.toggle('hidden',!filtered.includes(x)));filtered.sort((a,b)=>exact&&a.dataset.id===raw?-1:b.dataset.id===raw?1:0).forEach(x=>x.parentNode.appendChild(x));count.textContent=`${filtered.length} / ${items.length}`;history.replaceState(null,'',raw?'#q='+encodeURIComponent(input.value):location.pathname)};let timer;input.addEventListener('input',()=>{clearTimeout(timer);timer=setTimeout(run,150)});[type,access].forEach(x=>x.addEventListener('change',run));clear.onclick=()=>{input.value='';run();input.focus()};document.addEventListener('keydown',e=>{if(e.key==='/'&&document.activeElement!==input){e.preventDefault();input.focus()}if(e.key==='Escape'){input.value='';run()}});if(location.hash.startsWith('#q='))input.value=decodeURIComponent(location.hash.slice(3));run();})();"""
    types = sorted({p.datatype for p in parameters if p.datatype})
    type_options = '<option value="">All data types</option>' + ''.join(f'<option>{html_lib.escape(x)}</option>' for x in types)
    return f'<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ACOPOS Parameters</title><style>{css}</style></head><body><header><h1>ACOPOS Parameter List</h1><small>Source: B&amp;R Automation Help, version {html_lib.escape(version)}, generated {generated} | {len(parameters)} parameters</small></header><main><div class="tools"><input id="search" autofocus placeholder="Search parameters..." aria-label="Search"><select id="type" aria-label="Data type">{type_options}</select><select id="access" aria-label="Access"><option value="">All access</option><option>RD</option><option>WR</option><option>RW</option></select><button id="clear" type="button">Clear</button><output id="count"></output></div>{''.join(rows)}</main><script>{js}</script></body></html>'


def main() -> int:
    """Parse CLI options, download help content, and write generated output.

    Returns:
        Process exit code: 0 for success, 1 for a fatal overview/constants failure,
        or 2 when more than five percent of parameter detail pages are missing.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-o", "--output", type=Path, default=Path("acopos_parameters.html"))
    parser.add_argument("--lang", default="EN"); 
    parser.add_argument("--version", default="6"); 
    parser.add_argument("--workers", type=int, default=8); 
    parser.add_argument("--delay", type=float, default=0.1); 
    parser.add_argument("--cache-dir", type=Path, default=Path(".cache")); 
    parser.add_argument("--help-dir", type=Path, default=Path("acopos_help")); 
    parser.add_argument("--no-cache", action="store_true"); 
    parser.add_argument("--refresh", action="store_true"); 
    parser.add_argument("--limit", type=int); 
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s: %(message)s")
    client = HelpClient(args.lang, args.version, args.cache_dir, not args.no_cache, args.refresh, max(0, args.delay), args.workers)
    try:
        # Load shared reference data before fetching individual parameter detail pages.
        help_dir = download_help_tree(client, args.help_dir)
        constants = parse_constants(client.get("libraries/ncglobal/dataconst/ncconstants.html"))
        refs = parse_overview(client.get("ncsoftware/acp10_parameter/acp10_parameter_nach_nummern.htm"))
    except Exception as exc:
        LOG.error("Overview or constants could not be loaded: %s", exc); return 1
    refs = refs[:args.limit] if args.limit else refs
    param_ids = {Path(ref.source_path).name: ref.id for ref in refs}
    parameters: list[Parameter] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(client.get, ref.source_path): ref for ref in refs}
        for future in as_completed(futures):
            ref = futures[future]
            try:
                parameter = parse_parameter(future.result(), ref)
                for key, value in list(parameter.fields.items()): parameter.fields[key] = sanitize_and_enrich(value, constants, param_ids, help_dir, args.output)
                parameter.description_html = sanitize_and_enrich(parameter.description_html, constants, param_ids, help_dir, args.output)
            except Exception as exc:
                LOG.warning("Details missing for %s: %s", ref.id, exc)
                parameter = Parameter(ref.id, ref.define, ref.title, ref.datatype, "", ref.source_path, missing=True)
            parameter.search_text = " ".join([parameter.define, parameter.title, parameter.datatype, parameter.access, *(plain_html_text(value) for value in parameter.fields.values()), plain_html_text(parameter.description_html)])
            parameters.append(parameter)
    parameters.sort(key=lambda item: item.id)
    missing = sum(p.missing for p in parameters)
    args.output.write_text(render_html(parameters, args.lang, args.version), encoding="utf-8")
    LOG.info("%s written with %d parameters", args.output, len(parameters))
    # Exit code 2 flags output where more than five percent of detail pages failed.
    return 2 if parameters and missing / len(parameters) > 0.05 else 0


if __name__ == "__main__":
    raise SystemExit(main())
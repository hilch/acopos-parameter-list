# Specification: ACOPOS Parameter and Error Search

## 1. Purpose

A Python script (`generate_acopos_parameter_list.py`) downloads ACOPOS documentation from the B&R
Automation Help (version 6, language EN by default; both are configurable), enriches it, and
generates searchable HTML files (`acopos_parameters.html` and `acopos_errors.html`) plus a local
copy of the sections "ACOPOS drive functions" and "ACOPOS Error Texts" including HTML, CSS, and
JavaScript.

Motivation: The online help spreads the information across more than 1000 individual pages, is only
usable online, and shows NC constants without their numeric values.

## 2. Data Sources

The Automation Help is a single-page application. The HTML contents are **not** delivered under the
fragment URL but loaded via a JSON API.

### 2.1 Content API (verified)

```
PUT https://help.br-automation.com/api/v1/content/path
Content-Type: application/json

{"id":"","lang":"EN","version":"6","path":"<relative path>"}
```

Response (JSON):

| Field    | Meaning                                            |
|----------|----------------------------------------------------|
| `id`     | internal content ID                                |
| `html`   | complete HTML document of the help page            |
| `text`   | plain text variant                                 |
| `css`    | additional CSS information (usually empty)         |
| `js`     | additional JS information (usually empty)          |
| `level`  | numeric tree level                                 |
| `error`  | error information                                  |

The `path` corresponds to the part of the UI URL after `#/<lang>/<version>/`.

### 2.2 Required Paths

| Purpose               | `path`                                                        |
|-----------------------|---------------------------------------------------------------|
| Parameter overview    | `ncsoftware/acp10_parameter/acp10_parameter_nach_nummern.htm`  |
| Parameter detail page | `ncsoftware/acp10_parameter/html/<name>.htm` (from overview)   |
| NC constants          | `libraries/ncglobal/dataconst/ncconstants.html`                |

## 3. Structure of the Source Pages

### 3.1 Overview Page

Contains a table `table.nolines_tab`; each row is a link:

```html
<td class="content-level0 odd">
  <p><a href="#/en/6/ncsoftware/acp10_parameter/html/safemc_status.htm">
     4 - UDINT - SAFEMC_STATUS: SafeMOTION: Status</a></p>
</td>
```

The link text follows this pattern:

```
<ID> - <data type> - <define>: <title>
```

The language and version segments in links vary with the selected `--lang` and `--version` values;
the examples in this section use EN/version 6.

The data type may contain additional information and spaces, e.g. `DINT (internal I4+R4)`.
It is therefore captured up to the separator before `define`; for the heading and filtering, only
the base type before the first opening parenthesis is used.

Suggested regex:
`^\s*(?P<id>\d+)\s*-\s*(?P<datatype>.+?)\s*-\s*(?P<define>\w+)\s*:\s*(?P<title>.+)$`

If the text does not match the pattern, store the raw text as the title, use ID `-1` and empty
overview metadata, and log a warning. The detail page may still provide parsed metadata; generation
does not abort for an unrecognized overview row.

### 3.2 Parameter Detail Page (example `cmd_phasing.htm`)

```html
<h1>334: Motor: Phasing: Command</h1>
<table class="parameter_tab font-std">
  <tr><td><p><b>Define:</b></p></td>     <td><p>CMD_PHASING</p></td></tr>
  <tr><td><p><b>Access:</b></p></td>     <td><p>WR</p></td></tr>
  <tr><td><p><b>Unit:</b></p></td>       <td><p></p></td></tr>
  <tr><td><p><b>Data type:</b></p></td>  <td><p>UINT</p></td></tr>
  <tr><td><p><b>Value range:</b></p></td><td><p>ncSWITCH_ON, ncSWITCH_OFF</p></td></tr>
</table>
<p><b>Description:</b><br>See
   <a href="#/en/6/ncsoftware/acp10_drivefunctions/antriebsidentifikation/einphasen/einphasen_.html">
   Motor phasing</a><br></p>
```

The field list is **not fixed**. Labels that occur include: `Define`, `Access`, `Unit`,
`Data type`, `Value range`, `Default value`, `Cyclic`, `Note`, `Description`; German pages may use
`Beschreibung` for the description label.
The script must take over all label/value pairs generically, not only the known ones.

### 3.3 NC Constants (`ncconstants.html`)

Table rows with four columns:

```html
<tr>
  <td class="parameter_tab"><p class="pre"><a name="ncSWITCH_ON">ncSWITCH_ON</a></p></td>
  <td class="parameter_tab"><p class="pre"><span class="kwd">UINT</span></p></td>
  <td class="parameter_tab"><p class="dtr">0x0102</p></td>
  <td class="parameter_tab"><p class="dtl">Switch on</p></td>
</tr>
```

Columns: name, data type, value (decimal **or** hexadecimal `0x…`), description.

## 4. Processing Steps

1. **Load constants** → dictionary of `NcConstant` values indexed by name.
   - `0x…` is additionally computed as a decimal value (`int(v, 16)`).
   - For duplicate names with the same value: store once.
     For duplicate names with a different value: keep the first definition and emit a warning.
2. **Load overview** → list of all parameters (ID, data type, define, title, detail path).
3. **Load detail pages** (in parallel, see §7) → per parameter the field pairs + description block.
4. **Post-processing** (see §5).
5. **Render** the parameter and error-list HTML files (see §6).

## 5. Post-Processing Rules

### 5.1 Annotate NC Constants

In parameter field and description text copied from the help, every occurrence of a known constant
name is supplemented with the numeric value in parentheses:

```
ncSWITCH_ON  →  ncSWITCH_ON (0x0102 / 258)
ncSWITCH_OFF →  ncSWITCH_OFF (0x0103 / 259)
```

Rules:

- Detection via word boundaries: `\bnc[A-Z0-9_]+\b`; replace only if the name is in the constants dictionary.
- **Longest match first** (e.g. `ncSWITCH_ON` before `ncSWITCH`) so that no partial matches occur.
- Replacement happens **only in text nodes**, never in attribute values (hrefs, classes) or in
  already inserted annotations (no multiple annotation).
- If the original value was hexadecimal, `(<hex> / <decimal>)` is output; if it was decimal, only `(<decimal>)`.
- Unknown `nc…` identifiers remain unchanged; they are collected and logged as a warning at the end.
- The annotation gets the CSS class `nc-const-value` so that it can be visually set apart, and a
  `title` attribute with the description of the constant.

### 5.2 Links

- Links to `#/<lang>/<version>/ncsoftware/acp10_drivefunctions/…` and
  `#/<lang>/<version>/ncsoftware/acp10_errortext/…` are rewritten to corresponding local HTML
  files under `acopos_help/`. The link structure within these two help trees is also resolved locally.
- Links to other parameter pages (`…/acp10_parameter/html/<name>.htm`) are rewritten into **internal**
  anchors of the generated file: `#param-<ID>`.
- All remaining Automation Help routes continue to point to the original help site using their
  language and version.
- Referenced CSS, JavaScript, and image assets in the local help trees are downloaded and rewritten
  to relative local paths; failed asset downloads are logged and may leave missing local files.
- The click interceptor `iframe-communicator.js` is not embedded into the local individual files,
  because it blocks normal browser navigation with `preventDefault()`. Missing relative links in
  downloaded help pages are removed; parameter-detail links can still point to a missing local page
  if that page could not be downloaded. Non-local SPA links point to the original help site.

### 5.3 HTML Sanitizing (Security)

The following are removed from the adopted HTML:
`<script>`, `<style>`, `<link>`, `<meta>`, `<iframe>`, `<object>`, `<embed>`, `<form>`
as well as all `on*` event handler attributes and `javascript:` URLs.

Allowed tags (whitelist): `p, br, b, strong, i, em, u, sub, sup, ul, ol, li, table, thead,
tbody, tr, th, td, a, span, div, code, pre, h2, h3, h4, img`.
Allowed source attributes: `href`, `title`, `class`, `colspan`, `rowspan`, `src`, and `alt`. `src`
is retained only for HTTPS URLs. `javascript:` links and links to the B&R blob-storage host are
removed, and recognized help routes are rewritten; other `href` schemes are not generally
filtered. `target` and `rel` are added to external Automation Help links when needed.

All values that do not originate from the HTML (title, define, IDs) are escaped during rendering.

## 6. Output: HTML Files and Local Help

### 6.1 Hard Requirements

- **Two searchable HTML files**: `acopos_parameters.html` and `acopos_errors.html`, each with inline
  CSS and JavaScript and no external stylesheet or script dependency. The parameter list links to
  the separately stored `acopos_help/` tree. HTTPS image URLs from parameter content are retained,
  so external network requests are possible when such images are present. External help links also
  require a connection when opened.
- Generated content and successfully downloaded local help are usable offline, UTF-8,
  `<!DOCTYPE html>`, except for retained remote HTTPS images or unavailable local assets. The HTML
  language attribute is currently fixed to `lang="en"`, including when source content is localized.
- Target size: the file may be several MB in size; a load time of < 2 s is aimed for.

### 6.2 Layout

```
Header       : title, generation date, source version, number of parameters
Search bar   : text field (autofocus), match counter, "Clear" button
Filters      : data type (select), access RD/WR/RW (select)
Result list  : one row per parameter, expandable
```

When their downloads succeed, the two entry points
`ncsoftware/acp10_drivefunctions/acopos_index.html` and
`ncsoftware/acp10_errortext/index.htm`, the pages linked from them, and their referenced
CSS/JavaScript assets are stored under `acopos_help/`. Failed page and asset downloads are logged,
and the local tree may be incomplete.

### 6.3 Presentation of a Parameter

Each parameter is a `<details id="param-<ID>" class="param" data-…>` element:

- `<summary>`: `<ID> — <data type> — <define>: <title>`
- Expanded content: definition table (all label/value pairs) + description block,
  including annotated NC constants and preserved drive functions links.

Data attributes for the search:
`data-id`, `data-define`, `data-title`, `data-datatype`, `data-access`, `data-search`
(the latter = normalized full text from all fields, lowercase).

### 6.4 Search Behavior (inline JavaScript, no framework)

- Client-side filtering via `data-search`, case-insensitive.
- Multiple terms separated by spaces are combined with AND.
- A query consisting only of digits uses normal text filtering and sorts exact ID matches to the top.
- The prefixes `define:`, `id:`, `type:` select a field for targeted search. `define` and `type`
  comparisons use their source casing, while the query is lowercased, so those targeted searches can
  be case-sensitive in practice.
- Input is debounced by 150 ms.
- The search term is mirrored in the URL fragment (`#q=…`) so that searches can be linked.
- Keyboard: `/` focuses the search field, `Esc` clears it.
- Without an active search, all parameters are displayed (collapsed).

### 6.5 Error List

`acopos_errors.html` is generated from numeric `.htm`/`.html` detail pages under
`acopos_help/ncsoftware/acp10_errortext/html/`. Each expandable error shows its signed error code,
bracketed error number, error text, severity, and description, with a relative link to the complete
local help detail page. Independent controls filter by error number (matching either displayed
number), error text, severity, and description. Filtering is case-insensitive and runs locally in
the generated page.

## 7. Non-Functional Requirements

- **Python** ≥ 3.10, type annotations, `argparse` CLI.
- **Dependencies**: `requests`, `beautifulsoup4`, `lxml`. To be documented in `requirements.txt`.
- **Concurrency**: `concurrent.futures.ThreadPoolExecutor`, default 8 workers, configurable.
- **Rate limiting**: configurable delay (default 100 ms) between content API requests; asset GET
  requests are not subject to this delay.
- **Retry**: up to 3 attempts with exponential backoff (0.5 and 1 second) for request, response,
  and API errors.
- **Cache**: HTML responses are optionally stored under a SHA-1 filename derived from language,
  help version, and content path. By default they are read and written; `--refresh` bypasses cache
  reads but replaces entries, while `--no-cache` bypasses both reads and writes.
- **Logging** via `logging` (INFO by default, `--verbose` for DEBUG), warnings for unknown
  constants, parse errors, and failed pages.
- **Robustness**: individual failed detail pages do not cause an abort; the parameter is included
  with the note "details not available". Exit code 0 on success,
  1 on abort (overview or constants not loadable), 2 if > 5 % of the detail pages are missing.

## 8. CLI

```
python generate_acopos_parameter_list.py [options]

  -o, --output PATH      Output file (default: acopos_parameters.html)
  --errors-output PATH  Error-list file (default: acopos_errors.html)
      --lang CODE        Language code for the API (default: EN)
      --version VER      Help version (default: 6)
      --workers N        Parallel downloads (default: 8)
      --delay SEC        Delay per request (default: 0.1)
      --cache-dir PATH   Cache directory (default: .cache)
      --help-dir PATH    Local help directory (default: acopos_help; existing directory is replaced)
      --no-cache         Do not read or write the cache
      --refresh          Ignore cached entries and download them again
      --limit N          Process only the first N parameters (development/test)
  -v, --verbose          Debug logging
```

## 9. Current Module Structure

| Class / function | Task |
|---|---|
| `HelpClient` | API and asset access, retry, rate limit, cache |
| `NcConstant`, `ParameterRef`, `Parameter`, `ErrorEntry` | Parsed constants, overview references, parameter data, and error data |
| `parse_constants(source)` | Parse NC constants into `NcConstant` values |
| `parse_overview(source)` | Parse overview into `ParameterRef` values |
| `parse_parameter(source, ref)` | Parse parameter fields and description |
| `parse_error_pages(help_dir)` | Parse numeric error pages from the local help tree |
| `sanitize_and_enrich(...)` | Sanitize fragments, rewrite links, and annotate constants |
| `download_help_tree(client, output_dir)` | Download local help pages and assets |
| `render_html(parameters, lang, version)` | Generate the searchable HTML document |
| `render_errors(errors, lang, version)` | Generate the searchable error-list HTML document |
| `main()` | Parse CLI options and run the generator |

Data classes:

```python
from dataclasses import dataclass, field

@dataclass
class NcConstant:
    name: str
    datatype: str
    raw_value: str
    decimal: int | None
    description: str

@dataclass
class ParameterRef:
    id: int
    datatype: str
    define: str
    title: str
    source_path: str

@dataclass
class Parameter:
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
```

1. The generated parameter-list file has inline CSS/JavaScript and no external stylesheet/script
  dependency. HTTPS images may cause external requests; external help links are followed only when
  clicked.
2. Parameter 334 (`CMD_PHASING`) is included and shows
   `Value range: ncSWITCH_ON (0x0102 / 258), ncSWITCH_OFF (0x0103 / 259)`.
3. The link "Motor phasing" in parameter 334 points to a local file under
  `acopos_help/ncsoftware/acp10_drivefunctions/…`.
4. Searching for `phasing` returns parameter 334; searching for `334` returns it as the first match.
5. The number of parameters in the output matches the number of rows of the overview page.
6. Imported parameter fields/descriptions contain no executable `<script>`, `on*` event attributes,
   or `javascript:` links. The locally stored help pages contain the required JavaScript and CSS
   files and may keep those references.
7. When downloads succeed, the help entry points and CSS/JavaScript/image assets are stored under
   `acopos_help/`; asset download failures are logged. Rewritten help-page resource attributes do
   not point directly to `buronlinehelpprod.blob.core.windows.net`.
8. A second run with a populated cache produces a byte-identical file
   (apart from the generation timestamp).

## 11. Legal Notice

The contents originate from the B&R Automation Help and are protected by copyright.
The generated file identifies B&R Automation Help and the selected version in its header; it does
not currently provide a direct source link there. It is intended exclusively for personal or
internal use.
Redistribution is only permitted with the consent of B&R.

[![License: GPL v3](https://img.shields.io/badge/License-GPL%20v3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0) [![Made For B&R](https://github.com/hilch/BandR-badges/blob/main/Made-For-BrAutomation.svg)](https://www.br-automation.com)

# ACOPOS Parameter List

Builds an offline ACOPOS reference from the B&R Automation Help: self-contained, searchable HTML
parameter and error lists, plus local copies of the "ACOPOS drive functions" and "ACOPOS Error
Texts" sections. API responses are cached locally by default to speed up repeated runs.

The online help spreads the information across more than 1000 individual pages, is only usable
while online, and shows NC constants without their numeric values. This tool collects and enriches
the pages, resolving NC constants to their numeric values and linking related parameter and help
content.

## Features

- Downloads the parameter overview, all parameter detail pages, and the NC constants via the
  Automation Help content API
- Resolves NC constants to their numeric values directly in the text
- Cross-links parameters and rewrites help links to the local copy
- Client-side search plus filters for data type and access mode, all embedded in a single file
- A separate searchable error list, filterable by error number, error text, severity, and description
- Local response cache keyed by language, help version, and content path so repeated runs are fast

## Requirements

- Python 3.10 or newer
- Internet access to `help.br-automation.com`

## Quick start (Windows / PowerShell)

```powershell
.\run_acopos_parameter_list.ps1
```

The script creates the virtual environment `.venv` if it does not exist yet, installs the
dependencies from `requirements.txt`, and then runs the generator. Additional arguments are
forwarded to the Python script:

```powershell
.\run_acopos_parameter_list.ps1 --lang DE --verbose
```

This requests German help content and enables detailed diagnostic logging.

If script execution is blocked, allow it for the current session:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
```

## Manual setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python generate_acopos_parameter_list.py
```

## Command line options

| Option | Default | Description |
|---|---|---|
| `-o`, `--output` | `acopos_parameters.html` | Path of the generated HTML file |
| `--errors-output` | `acopos_errors.html` | Path of the generated error-list HTML file |
| `--lang` | `EN` | Help language |
| `--version` | `6` | Automation Help version |
| `--workers` | `8` | Number of parallel downloads |
| `--delay` | `0.1` | Delay in seconds between requests |
| `--cache-dir` | `.cache` | Directory for the response cache |
| `--help-dir` | `acopos_help` | Target directory for the local help copy |
| `--no-cache` | off | Disable the cache |
| `--refresh` | off | Ignore cached entries and download again |
| `--limit` | – | Process only the first N parameters (useful for testing) |
| `-v`, `--verbose` | off | Verbose logging |

## Caching

API responses are cached in `.cache/` by default. Cache entries are keyed by language, help version,
and content path, so different languages and versions use separate responses. On later runs, valid
cached responses are reused to reduce network requests and speed up generation.

- Default: read cached responses when available and save newly downloaded responses.
- `--refresh`: ignore cached responses for this run, download them again, and update the cache.
- `--no-cache`: do not read from or write to the cache. All API content is fetched again, which
  requires network access and can make generation slower. Existing cache files are not deleted.

## Output

- `acopos_parameters.html` – standalone searchable parameter list
- `acopos_errors.html` – standalone searchable error list
- `acopos_help/` – local copy of the referenced help pages including CSS, JS, and images
- `.cache/` – cached API responses

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | Overview page or NC constants could not be loaded |
| `2` | More than 5 % of the parameters are incomplete |

## Notes

The generated content originates from the B&R Automation Help and remains the property of
B&R Industrial Automation. This project only provides the tooling and is not affiliated with
or endorsed by B&R.

## License

GPL 3 – see [LICENSE](LICENSE).

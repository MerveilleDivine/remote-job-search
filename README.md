<p align="center">
  <img src="./assets/readme-banner.svg" width="100%" alt="Remote Job Search" />
</p>

# Remote Job Search

I built a Python client to search job listings from the command line or a browser, filter remote roles, and download the results as CSV. Both interfaces use the same JSearch client, so validation, retries, normalization, and duplicate handling stay consistent.

[Portfolio](https://www.mervine.dev) · [GitHub](https://github.com/MerveilleDivine)

## Try it locally

Requires Python 3.10+ and a JSearch subscription/key from RapidAPI. Searches use your API quota. The application reads structured API responses; it does not scrape job-board HTML.

```bash
git clone https://github.com/MerveilleDivine/remote-job-search.git
cd remote-job-search
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

On Windows, activate with `.venv\Scripts\activate` and copy with `copy .env.example .env`.

Set `API_KEY` in `.env`, which is ignored by Git. Then search:

```bash
python code/scraper.py "backend engineer" --country lt --remote-only --output results/jobs.csv
```

Or open the Gradio interface:

```bash
python code/app.py
```

| CLI option | Behavior |
|---|---|
| `keyword` | Required search terms: role, technology, location, or a combination |
| `--country` | Two-letter country code; defaults to `us`. Pass `--country ""` for worldwide results |
| `--pages` | Number of pages requested from JSearch, from 1 to 10; defaults to 1 |
| `--remote-only` | Keep listings whose API response marks them as remote |
| `--output` | CSV path; otherwise generated from the query |

The browser interface starts with worldwide results and the remote filter enabled. Output includes title, employer, location, remote status, employment type, application link, and posting date.

## Implementation decisions

- HTTP timeouts and bounded retries handle connection failures, rate limits, and transient server errors. Authentication and malformed-response errors return a readable message.
- The client accepts an injected HTTP session. Tests can exercise success and failure paths without real credentials or paid requests.
- Duplicate detection preserves result order. It compares application links without changing case-sensitive URL paths or query values; listings without links use title, company, and location.
- CSV exports prefix formula-like values with an apostrophe so untrusted listing text is treated as text in spreadsheet applications. Search results remain unchanged in memory.
- Browser downloads use separate temporary directories so one search does not overwrite another user's CSV.

## Check the code

```bash
python -m compileall -q code tests
python -m unittest discover -s tests -v
```

GitHub Actions runs these checks on Python 3.10 and 3.12. The tests cover input validation, request parameters, remote filtering, duplicate handling, API errors, CSV output, and browser callbacks. They do not contact JSearch.

| Location | Purpose |
|---|---|
| `code/scraper.py` | API client, validation, normalization, CSV export, and CLI |
| `code/app.py` | Gradio interface and download callbacks |
| `tests/` | Offline unit tests |
| `.env.example` | API key configuration template |

## Limits

This is a local search utility. It has no accounts, database, saved searches, or scheduled alerts. Results depend on JSearch coverage, and a remote flag does not establish a role's hiring eligibility in your country. Country codes are checked for format; city names belong in the query.

[MIT License](LICENSE).

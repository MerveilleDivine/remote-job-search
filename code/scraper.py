"""JSearch API client and command-line interface.

The module keeps network access, validation, normalization, and file export in
small functions so they can be reused by the CLI, the Gradio app, and tests.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit

import requests
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

load_dotenv()

API_URL = "https://jsearch.p.rapidapi.com/search"
API_HOST = "jsearch.p.rapidapi.com"
DEFAULT_TIMEOUT_SECONDS = 15
MAX_PAGES = 10
CSV_FIELDS = (
    "Job Title",
    "Company",
    "Location",
    "Remote",
    "Employment Type",
    "Link",
    "Date Posted",
)


class JSearchError(RuntimeError):
    """Raised when JSearch cannot return a usable result."""


class ConfigurationError(JSearchError):
    """Raised when required local configuration is missing."""


def validate_query(query: str) -> str:
    """Return a normalized search query or raise a helpful error."""
    if not isinstance(query, str):
        raise ValueError("Search keywords must be text.")
    normalized = " ".join(query.split())
    if not normalized:
        raise ValueError("Search keywords cannot be empty.")
    return normalized


def validate_country(country: str | None) -> str:
    """Validate an optional ISO 3166-1 alpha-2 country code."""
    normalized = (country or "").strip().lower()
    if normalized and not re.fullmatch(r"[a-z]{2}", normalized):
        raise ValueError("Country must be blank or a two-letter code such as us, gb, or ca.")
    return normalized


def validate_positive_int(value: Any, name: str, maximum: int | None = None) -> int:
    """Convert a value to a bounded positive integer."""
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a positive integer.")

    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"{name} must be a positive integer.") from error

    if number < 1 or str(value).strip() not in {str(number), f"{number}.0"}:
        raise ValueError(f"{name} must be a positive integer.")
    if maximum is not None and number > maximum:
        raise ValueError(f"{name} cannot be greater than {maximum}.")
    return number


def create_retrying_session() -> requests.Session:
    """Create a session that retries transient GET failures with backoff."""
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.mount("https://", adapter)
    return session


def _location(job: Mapping[str, Any]) -> str:
    parts = [job.get("job_city"), job.get("job_state"), job.get("job_country")]
    location = ", ".join(str(part).strip() for part in parts if part)
    if location:
        return location
    return "Remote" if job.get("job_is_remote") else "Not specified"


def normalize_job(job: Mapping[str, Any]) -> dict[str, str]:
    """Reduce a JSearch job object to the stable fields used by this project."""
    return {
        "Job Title": str(job.get("job_title") or "Not specified").strip(),
        "Company": str(job.get("employer_name") or "Not specified").strip(),
        "Location": _location(job),
        "Remote": "Yes" if job.get("job_is_remote") else "No",
        "Employment Type": str(job.get("job_employment_type") or "Not specified").strip(),
        "Link": str(job.get("job_apply_link") or job.get("job_google_link") or "").strip(),
        "Date Posted": str(job.get("job_posted_at_datetime_utc") or "Not specified").strip(),
    }


def deduplicate_jobs(jobs: Sequence[dict[str, str]]) -> list[dict[str, str]]:
    """Preserve result order while removing repeated listings."""
    unique_jobs: list[dict[str, str]] = []
    seen: set[tuple[str, ...]] = set()

    for job in jobs:
        link = job.get("Link", "").strip()
        if link:
            # URL hosts are case-insensitive; paths and query values are not.
            try:
                parsed = urlsplit(link)
                link = urlunsplit(
                    (parsed.scheme.lower(), parsed.netloc.lower(),
                     parsed.path, parsed.query, parsed.fragment)
                )
            except ValueError:
                pass  # Preserve a malformed source URL rather than losing the listing.
            identity = ("link", link)
        else:
            identity = (
                "listing",
                job.get("Job Title", "").casefold(),
                job.get("Company", "").casefold(),
                job.get("Location", "").casefold(),
            )

        if identity in seen:
            continue
        seen.add(identity)
        unique_jobs.append(job)

    return unique_jobs


class JSearchClient:
    """Small, injectable client for the JSearch search endpoint."""

    def __init__(
        self,
        api_key: str | None = None,
        session: requests.Session | None = None,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.api_key = (api_key or os.getenv("API_KEY") or "").strip()
        if not self.api_key:
            raise ConfigurationError(
                "No JSearch API key found. Copy .env.example to .env and set API_KEY."
            )
        self.session = session or create_retrying_session()
        self.timeout = validate_positive_int(timeout, "Timeout")

    def search(
        self,
        query: str,
        page: int = 1,
        country: str = "",
        num_pages: int = 1,
        remote_only: bool = False,
    ) -> list[dict[str, str]]:
        """Search JSearch and return normalized, deduplicated listings."""
        params = {
            "query": validate_query(query),
            "page": validate_positive_int(page, "Page"),
            "num_pages": validate_positive_int(num_pages, "Pages", maximum=MAX_PAGES),
            "country": validate_country(country),
            "date_posted": "all",
        }
        headers = {
            "x-rapidapi-host": API_HOST,
            "x-rapidapi-key": self.api_key,
        }

        try:
            response = self.session.get(
                API_URL,
                headers=headers,
                params=params,
                timeout=self.timeout,
            )
            response.raise_for_status()
        except requests.Timeout as error:
            raise JSearchError("JSearch timed out. Please try again.") from error
        except requests.HTTPError as error:
            status = error.response.status_code if error.response is not None else None
            if status in {401, 403}:
                message = "JSearch rejected the API credentials. Check API_KEY and your RapidAPI access."
            elif status == 429:
                message = "JSearch rate limit reached. Wait briefly and try again."
            else:
                message = f"JSearch returned HTTP {status}." if status else "JSearch request failed."
            raise JSearchError(message) from error
        except requests.RequestException as error:
            raise JSearchError("Could not reach JSearch. Check your connection and try again.") from error

        try:
            payload = response.json()
        except ValueError as error:
            raise JSearchError("JSearch returned an invalid JSON response.") from error

        raw_jobs = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(raw_jobs, list):
            raise JSearchError("JSearch response did not contain a job list.")

        normalized = [normalize_job(job) for job in raw_jobs if isinstance(job, Mapping)]
        if remote_only:
            normalized = [job for job in normalized if job["Remote"] == "Yes"]
        return deduplicate_jobs(normalized)


def fetch_jobs_api(
    query: str,
    page: int = 1,
    country: str = "",
    num_pages: int = 1,
    remote_only: bool = False,
    *,
    api_key: str | None = None,
    session: requests.Session | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> list[dict[str, str]]:
    """Compatibility wrapper shared by the CLI and Gradio app."""
    return JSearchClient(api_key=api_key, session=session, timeout=timeout).search(
        query=query,
        page=page,
        country=country,
        num_pages=num_pages,
        remote_only=remote_only,
    )


def default_output_filename(query: str) -> str:
    """Build a portable CSV filename from a query."""
    slug = re.sub(r"[^a-z0-9]+", "_", validate_query(query).casefold()).strip("_")
    return f"{slug or 'jobs'}_jobs.csv"


def save_jobs_to_csv(
    jobs: Sequence[Mapping[str, str]],
    filename: str | os.PathLike[str],
    *,
    announce: bool = True,
) -> Path | None:
    """Write normalized jobs to CSV and return the created path."""
    if not jobs:
        if announce:
            print("No jobs found. Try another keyword.")
        return None

    destination = Path(filename)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open(mode="w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        # API text is untrusted. Quoting alone does not stop spreadsheet formulas.
        writer.writerows(
            {
                field: "'" + value
                if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@"))
                else value
                for field, value in job.items()
            }
            for job in jobs
        )

    if announce:
        print(f"Saved {len(jobs)} jobs to {destination}")
    return destination


def _positive_pages(value: str) -> int:
    try:
        return validate_positive_int(value, "Pages", maximum=MAX_PAGES)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def _country_code(value: str) -> str:
    try:
        return validate_country(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Search remote roles with the JSearch API.")
    parser.add_argument("keyword", help="Role, technology, location, or combined search terms")
    parser.add_argument(
        "--pages",
        type=_positive_pages,
        default=1,
        help=f"Number of API pages to request, from 1 to {MAX_PAGES}",
    )
    parser.add_argument(
        "--country",
        type=_country_code,
        default="us",
        help="Two-letter country code, or an empty value for worldwide results",
    )
    parser.add_argument("--remote-only", action="store_true", help="Keep only listings marked remote")
    parser.add_argument("--output", help="Output CSV path")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        jobs = fetch_jobs_api(
            args.keyword,
            num_pages=args.pages,
            country=args.country,
            remote_only=args.remote_only,
        )
        destination = args.output or default_output_filename(args.keyword)
        save_jobs_to_csv(jobs, destination)
    except (JSearchError, ValueError) as error:
        print(f"Error: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

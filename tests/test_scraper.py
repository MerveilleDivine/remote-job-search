from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))

from scraper import (  # noqa: E402
    ConfigurationError,
    JSearchClient,
    JSearchError,
    deduplicate_jobs,
    default_output_filename,
    normalize_job,
    save_jobs_to_csv,
    validate_country,
    validate_positive_int,
    validate_query,
)


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            response = Mock(status_code=self.status_code)
            raise requests.HTTPError(response=response)

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class ValidationTests(unittest.TestCase):
    def test_query_is_trimmed_and_compacted(self):
        self.assertEqual(validate_query("  backend   engineer "), "backend engineer")

    def test_blank_query_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "cannot be empty"):
            validate_query("   ")

    def test_non_text_query_is_rejected(self):
        for value in (None, 42, True):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "must be text"):
                validate_query(value)

    def test_country_is_optional_but_must_be_two_letters(self):
        self.assertEqual(validate_country(" US "), "us")
        self.assertEqual(validate_country(""), "")
        with self.assertRaisesRegex(ValueError, "two-letter"):
            validate_country("usa")

    def test_positive_integer_rejects_fractional_and_out_of_range_values(self):
        self.assertEqual(validate_positive_int("2", "Pages", maximum=10), 2)
        with self.assertRaises(ValueError):
            validate_positive_int(0, "Pages")
        with self.assertRaises(ValueError):
            validate_positive_int(1.5, "Pages")
        with self.assertRaisesRegex(ValueError, "greater than 10"):
            validate_positive_int(11, "Pages", maximum=10)

    def test_non_finite_page_counts_raise_validation_errors(self):
        for value in (float("inf"), float("-inf"), float("nan")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_positive_int(value, "Pages")


class NormalizationTests(unittest.TestCase):
    def test_job_normalization_uses_stable_fields_and_fallbacks(self):
        result = normalize_job(
            {
                "job_title": "Backend Engineer",
                "employer_name": "Example Labs",
                "job_city": "Paris",
                "job_country": "FR",
                "job_is_remote": True,
                "job_employment_type": "FULLTIME",
                "job_google_link": "https://example.com/job/1",
            }
        )

        self.assertEqual(result["Location"], "Paris, FR")
        self.assertEqual(result["Remote"], "Yes")
        self.assertEqual(result["Link"], "https://example.com/job/1")
        self.assertEqual(result["Date Posted"], "Not specified")

    def test_duplicates_are_removed_by_link_or_listing_identity(self):
        jobs = [
            {"Job Title": "A", "Company": "C", "Location": "Remote", "Link": "https://x"},
            {"Job Title": "Different", "Company": "D", "Location": "US", "Link": "https://x"},
            {"Job Title": "B", "Company": "C", "Location": "Remote", "Link": ""},
            {"Job Title": "b", "Company": "c", "Location": "remote", "Link": ""},
        ]

        self.assertEqual(len(deduplicate_jobs(jobs)), 2)

    def test_url_identity_preserves_case_sensitive_paths_and_queries(self):
        links = [
            "https://EXAMPLE.com/jobs/ABC?id=A",
            "https://example.com/jobs/ABC?id=A",
            "https://example.com/jobs/abc?id=A",
            "https://example.com/jobs/ABC?id=a",
        ]
        jobs = [{"Link": link} for link in links]
        self.assertEqual(deduplicate_jobs(jobs), [jobs[0], jobs[2], jobs[3]])


class ClientTests(unittest.TestCase):
    def test_api_key_is_required_without_network_access(self):
        with self.assertRaisesRegex(ConfigurationError, "No JSearch API key"):
            JSearchClient(api_key="")

    def test_search_sends_validated_params_and_filters_remote_duplicates(self):
        session = Mock()
        session.get.return_value = FakeResponse(
            {
                "data": [
                    {
                        "job_title": "Backend Engineer",
                        "employer_name": "Example",
                        "job_is_remote": True,
                        "job_apply_link": "https://example.com/1",
                    },
                    {
                        "job_title": "Duplicate",
                        "employer_name": "Example",
                        "job_is_remote": True,
                        "job_apply_link": "https://example.com/1",
                    },
                    {
                        "job_title": "Office Role",
                        "employer_name": "Example",
                        "job_is_remote": False,
                        "job_apply_link": "https://example.com/2",
                    },
                ]
            }
        )
        client = JSearchClient(api_key="secret", session=session, timeout=9)

        jobs = client.search("  backend  engineer ", country=" US ", num_pages=2, remote_only=True)

        self.assertEqual(len(jobs), 1)
        _, kwargs = session.get.call_args
        self.assertEqual(kwargs["params"]["query"], "backend engineer")
        self.assertEqual(kwargs["params"]["country"], "us")
        self.assertEqual(kwargs["params"]["num_pages"], 2)
        self.assertEqual(kwargs["timeout"], 9)
        self.assertEqual(kwargs["headers"]["x-rapidapi-key"], "secret")

    def test_rate_limit_has_a_clear_error(self):
        session = Mock()
        session.get.return_value = FakeResponse({}, status_code=429)

        with self.assertRaisesRegex(JSearchError, "rate limit"):
            JSearchClient(api_key="secret", session=session).search("python")

    def test_invalid_payload_is_rejected(self):
        session = Mock()
        session.get.return_value = FakeResponse({"unexpected": []})

        with self.assertRaisesRegex(JSearchError, "did not contain a job list"):
            JSearchClient(api_key="secret", session=session).search("python")


class ExportTests(unittest.TestCase):
    def test_csv_neutralizes_formulas_without_mutating_results(self):
        for value in ("=1+1", "+1+1", "-1+1", "@SUM(1)", "  =1+1", "\t=1+1"):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as directory:
                job = normalize_job({"job_title": "Engineer"})
                job["Company"] = value
                destination = Path(directory) / "jobs.csv"
                save_jobs_to_csv([job], destination, announce=False)
                with destination.open(encoding="utf-8", newline="") as file:
                    exported = next(csv.DictReader(file))
                self.assertEqual(exported["Company"], "'" + value)
                self.assertEqual(exported["Job Title"], "Engineer")
                self.assertEqual(job["Company"], value)

    def test_default_filename_is_portable(self):
        self.assertEqual(default_output_filename("C++ / Backend"), "c_backend_jobs.csv")

    def test_csv_export_creates_parent_directory(self):
        jobs = [normalize_job({"job_title": "Engineer", "job_is_remote": True})]

        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "nested" / "jobs.csv"
            result = save_jobs_to_csv(jobs, destination, announce=False)

            self.assertEqual(result, destination)
            with destination.open(encoding="utf-8", newline="") as file:
                rows = list(csv.DictReader(file))
            self.assertEqual(rows[0]["Job Title"], "Engineer")
            self.assertEqual(rows[0]["Remote"], "Yes")


if __name__ == "__main__":
    unittest.main()

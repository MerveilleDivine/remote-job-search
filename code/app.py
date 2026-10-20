"""Gradio interface for the shared JSearch client."""

from __future__ import annotations

import tempfile
from pathlib import Path

import gradio as gr
import pandas as pd

from scraper import JSearchError, fetch_jobs_api, save_jobs_to_csv


def search_jobs(
    keywords: str,
    pages: int,
    country: str,
    remote_only: bool,
) -> tuple[str, pd.DataFrame | None]:
    """Run a validated search and prepare a table for the interface."""
    try:
        jobs = fetch_jobs_api(
            keywords,
            num_pages=pages,
            country=country,
            remote_only=remote_only,
        )
    except (JSearchError, ValueError) as error:
        return f"Error: {error}", None

    if not jobs:
        return "No jobs matched these filters. Try a broader query.", None

    return f"Found {len(jobs)} unique job listings.", pd.DataFrame(jobs)


def create_download(df: pd.DataFrame | None) -> str | None:
    """Create a unique CSV export so concurrent users never share one file."""
    if df is None or df.empty:
        return None

    temporary_directory = Path(tempfile.mkdtemp(prefix="remote-job-search-"))
    destination = temporary_directory / "jobs.csv"
    save_jobs_to_csv(df.to_dict(orient="records"), destination, announce=False)
    return str(destination)


with gr.Blocks(title="Remote Job Search") as demo:
    gr.Markdown(
        "# Remote Job Search\n"
        "Search JSearch listings, filter for explicitly remote roles, and export unique results."
    )

    with gr.Row():
        keywords = gr.Textbox(
            label="Keywords",
            placeholder="e.g. backend engineer, Python, React",
            value="remote developer",
        )
        pages = gr.Number(label="Pages", value=1, precision=0, minimum=1, maximum=10)
        country = gr.Textbox(label="Country code (blank for worldwide)", value="")

    remote_only = gr.Checkbox(label="Only listings marked remote", value=True)
    search_button = gr.Button("Search", variant="primary")
    status = gr.Markdown("Results will appear here.")
    results = gr.Dataframe(visible=False, interactive=False, label="Results")
    download = gr.DownloadButton("Download CSV", value=None, visible=False)

    def on_search(query: str, page_count: int, country_code: str, only_remote: bool):
        message, dataframe = search_jobs(query, page_count, country_code, only_remote)
        has_results = dataframe is not None and not dataframe.empty
        return (
            message,
            gr.update(visible=has_results, value=dataframe if has_results else None),
            gr.update(
                visible=has_results,
                value=create_download(dataframe) if has_results else None,
            ),
        )

    search_button.click(
        fn=on_search,
        inputs=[keywords, pages, country, remote_only],
        outputs=[status, results, download],
    )


if __name__ == "__main__":
    demo.launch()

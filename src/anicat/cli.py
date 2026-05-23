from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from .downloader import DEFAULT_CHUNK_SIZE
from .errors import AniCatError
from .logging_config import configure_logging
from .models import JobReport
from .options import DownloadOptions
from .progress import rich_download_progress
from .service import AniCatService
from .urls import split_urls


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser."""

    parser = argparse.ArgumentParser(
        prog="anicat",
        description="Download Anime1 episodes from episode or category URLs.",
    )
    parser.add_argument(
        "urls",
        nargs="*",
        help="Anime1 episode/category URLs. Whitespace and commas are accepted.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path.cwd() / "Anime1_Download",
        help="Output directory. Default: ./Anime1_Download",
    )
    parser.add_argument(
        "-c",
        "--concurrency",
        type=int,
        default=3,
        help="Concurrent episode downloads. Default: 3",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="HTTP read timeout in seconds. Default: 30",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=3,
        help="HTTP retry count. Default: 3",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=DEFAULT_CHUNK_SIZE,
        help=f"Download chunk size in bytes. Default: {DEFAULT_CHUNK_SIZE}",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Discard existing .part files instead of resuming.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace existing completed files.",
    )
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="Disable progress bar.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Show diagnostic logs. Use -vv for HTTP-level debug details.",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Suppress diagnostic logs except errors. Summary output is unchanged.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the AniCat CLI and return a process exit code."""

    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(verbose=args.verbose, quiet=args.quiet)
    try:
        options = options_from_args(args)
    except ValueError as error:
        print(f"argument error: {error}", file=sys.stderr)
        return 2

    input_urls = split_urls(args.urls)
    if not input_urls:
        if not sys.stdin.isatty():
            print("No URL provided.", file=sys.stderr)
            return 2
        try:
            input_urls = split_urls([input("? Anime1 URL：")])
        except EOFError:
            print("No URL provided.", file=sys.stderr)
            return 2
    if not input_urls:
        print("No URL provided.", file=sys.stderr)
        return 2

    service = AniCatService(options)

    try:
        episode_urls = service.collect_episode_urls(input_urls)
    except AniCatError as error:
        print(f"- {error}", file=sys.stderr)
        return 1

    if not episode_urls:
        print("- No episode found.", file=sys.stderr)
        return 1

    started_at = time.perf_counter()
    reports = run_downloads(service, options, episode_urls)
    elapsed = time.perf_counter() - started_at

    downloaded = [item for item in reports if item.result and item.result.status == "downloaded"]
    skipped = [item for item in reports if item.result and item.result.status == "skipped"]
    failed = [item for item in reports if item.error]

    for item in reports:
        if item.result:
            marker = "+" if item.result.status == "downloaded" else "="
            size = format_size(item.result.bytes_written)
            print(f"{marker} {item.result.status}: {item.result.episode.title} [{size}]")
            print(f"  -> {item.result.path}")
        else:
            print(f"- failed: {item.url}")
            print(f"  {item.error}")

    print(
        f"+ done in {elapsed:.2f}s: "
        f"{len(downloaded)} downloaded, {len(skipped)} skipped, {len(failed)} failed"
    )
    return 1 if failed else 0


def options_from_args(args: argparse.Namespace) -> DownloadOptions:
    """Convert parsed CLI arguments into service runtime options."""

    return DownloadOptions(
        output_dir=args.output,
        concurrency=args.concurrency,
        timeout=args.timeout,
        retries=args.retries,
        chunk_size=args.chunk_size,
        resume=not args.no_resume,
        overwrite=args.overwrite,
        progress=not args.no_progress,
    )


def run_downloads(
    service: AniCatService,
    options: DownloadOptions,
    episode_urls: list[str],
) -> list[JobReport]:
    """Run downloads with or without Rich progress rendering."""

    if not options.progress:
        return service.download_many(episode_urls)

    with rich_download_progress(len(episode_urls)) as progress:
        return service.download_many(
            episode_urls,
            on_progress=progress.on_progress,
            on_done=progress.on_done,
        )


def format_size(size: int) -> str:
    """Format byte count for the final textual summary."""

    units = ("B", "KB", "MB", "GB")
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.2f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size} B"

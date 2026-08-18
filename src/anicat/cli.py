from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .catalog import fetch_catalog, search_catalog
from .client import Anime1Client
from .constants import (
    DEFAULT_CHUNK_SIZE,
    DEFAULT_CONCURRENCY,
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_OUTPUT_DIR_NAME,
    DEFAULT_READ_TIMEOUT,
    DEFAULT_RETRIES,
)
from .errors import AniCatError
from .logging_config import configure_logging
from .models import AnimeEntry, JobReport
from .options import DownloadOptions
from .progress import rich_download_progress
from .service import AniCatService
from .urls import split_urls

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2
SEARCH_COMMAND = "search"


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser."""

    parser = argparse.ArgumentParser(
        prog="anicat",
        description="Download Anime1 episodes from episode or category URLs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Commands:\n"
            "  search <keyword>  List catalogue matches and their category URLs\n"
            "\n"
            "Exit codes:\n"
            "  0  All downloads completed or were skipped\n"
            "  1  At least one URL failed, no episode was found, or nothing matched\n"
            "  2  Invalid CLI usage or options"
        ),
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
        default=Path.cwd() / DEFAULT_OUTPUT_DIR_NAME,
        help=f"Output directory. Default: ./{DEFAULT_OUTPUT_DIR_NAME}",
    )
    parser.add_argument(
        "-c",
        "--concurrency",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help=f"Concurrent episode downloads. Default: {DEFAULT_CONCURRENCY}",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_READ_TIMEOUT,
        help=f"HTTP read timeout in seconds. Default: {DEFAULT_READ_TIMEOUT:g}",
    )
    parser.add_argument(
        "--connect-timeout",
        type=float,
        default=DEFAULT_CONNECT_TIMEOUT,
        help=f"HTTP connect timeout in seconds. Default: {DEFAULT_CONNECT_TIMEOUT:g}",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=DEFAULT_RETRIES,
        help=f"HTTP and stream retry count. Default: {DEFAULT_RETRIES}",
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
    verbosity_group = parser.add_mutually_exclusive_group()
    verbosity_group.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Show diagnostic logs. Use -vv for HTTP-level debug details.",
    )
    verbosity_group.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Suppress diagnostic logs except errors. Summary output is unchanged.",
    )
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="Show version and exit.",
    )
    return parser


def build_search_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the catalogue search subcommand."""

    parser = argparse.ArgumentParser(
        prog="anicat search",
        description="Search the Anime1 catalogue and print matching category URLs.",
    )
    parser.add_argument(
        "keyword",
        help="Substring matched against anime titles, ignoring case.",
    )
    verbosity_group = parser.add_mutually_exclusive_group()
    verbosity_group.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Show diagnostic logs. Use -vv for HTTP-level debug details.",
    )
    verbosity_group.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Suppress diagnostic logs except errors.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the AniCat CLI and return a process exit code."""

    arguments = list(sys.argv[1:] if argv is None else argv)
    # Dispatched before the download parser so that plain `anicat <url>` keeps
    # working without requiring a subcommand.
    if arguments and arguments[0] == SEARCH_COMMAND:
        return search_main(arguments[1:])

    parser = build_parser()
    args = parser.parse_args(arguments)
    configure_logging(verbose=args.verbose, quiet=args.quiet)
    try:
        options = options_from_args(args)
    except ValueError as error:
        print(f"argument error: {error}", file=sys.stderr)
        return EXIT_USAGE

    input_urls = split_urls(args.urls)
    if not input_urls:
        if not sys.stdin.isatty():
            print("No URL provided.", file=sys.stderr)
            return EXIT_USAGE
        try:
            input_urls = split_urls([input("? Anime1 URL：")])
        except EOFError:
            print("No URL provided.", file=sys.stderr)
            return EXIT_USAGE
    if not input_urls:
        print("No URL provided.", file=sys.stderr)
        return EXIT_USAGE

    service = AniCatService(options)

    try:
        episode_urls = service.collect_episode_urls(input_urls)
    except AniCatError as error:
        print(f"- {error}", file=sys.stderr)
        return EXIT_FAILURE

    if not episode_urls:
        print("- No episode found.", file=sys.stderr)
        return EXIT_FAILURE

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
    return EXIT_FAILURE if failed else EXIT_OK


def search_main(argv: Sequence[str]) -> int:
    """Run the catalogue search subcommand and return a process exit code."""

    parser = build_search_parser()
    args = parser.parse_args(argv)
    configure_logging(verbose=args.verbose, quiet=args.quiet)

    client = Anime1Client()
    try:
        entries = search_catalog(fetch_catalog(client), args.keyword)
    except AniCatError as error:
        print(f"- {error}", file=sys.stderr)
        return EXIT_FAILURE
    finally:
        client.close()

    if not entries:
        print(f"- No catalogue match for {args.keyword!r}.", file=sys.stderr)
        return EXIT_FAILURE

    for entry in entries:
        print(format_entry(entry))
        print(f"  -> {entry.url}")
    print(f"+ {len(entries)} result(s)")
    return EXIT_OK


def format_entry(entry: AnimeEntry) -> str:
    """Render one catalogue entry as a single summary line."""

    parts = (entry.episodes, f"{entry.year} {entry.season}".strip(), entry.subtitle_group)
    details = " · ".join(part for part in parts if part)
    return f"{entry.title} [{details}]"


def options_from_args(args: argparse.Namespace) -> DownloadOptions:
    """Convert parsed CLI arguments into service runtime options."""

    return DownloadOptions(
        output_dir=args.output,
        concurrency=args.concurrency,
        timeout=args.timeout,
        connect_timeout=args.connect_timeout,
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

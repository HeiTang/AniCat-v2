from __future__ import annotations

import logging

from rich.logging import RichHandler


def configure_logging(*, verbose: int, quiet: bool) -> None:
    """Configure CLI diagnostic logging without changing normal result output."""

    logging.basicConfig(
        level=log_level(verbose=verbose, quiet=quiet),
        format="%(message)s",
        handlers=[
            RichHandler(
                markup=False,
                rich_tracebacks=True,
                show_path=False,
                show_time=False,
            )
        ],
        force=True,
    )


def log_level(*, verbose: int, quiet: bool) -> int:
    """Return the root logging level for CLI verbosity flags."""

    if quiet:
        return logging.ERROR
    if verbose >= 2:
        return logging.DEBUG
    if verbose == 1:
        return logging.INFO
    return logging.ERROR

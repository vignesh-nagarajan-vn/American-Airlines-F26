"""Small cached downloader for public data sources."""

from __future__ import annotations

import logging
import time
from pathlib import Path

import requests

log = logging.getLogger(__name__)
HEADERS = {"User-Agent": "aaforecast-research/0.1 (python-requests)"}


def download(url: str, dest: Path, force: bool = False, retries: int = 3, timeout: int = 120) -> Path:
    """Download ``url`` to ``dest`` unless it already exists."""
    dest = Path(dest)
    if dest.exists() and dest.stat().st_size > 0 and not force:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    for attempt in range(1, retries + 1):
        try:
            with requests.get(url, headers=HEADERS, timeout=timeout, stream=True) as r:
                r.raise_for_status()
                with open(tmp, "wb") as fh:
                    for chunk in r.iter_content(chunk_size=1 << 20):
                        fh.write(chunk)
            tmp.replace(dest)
            log.info("Downloaded %s", url)
            return dest
        except requests.RequestException as exc:
            log.warning("Attempt %d for %s failed: %s", attempt, url, exc)
            time.sleep(2 * attempt)
    raise RuntimeError(f"Could not download {url}")


def get_text(url: str, timeout: int = 60) -> str:
    r = requests.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.text

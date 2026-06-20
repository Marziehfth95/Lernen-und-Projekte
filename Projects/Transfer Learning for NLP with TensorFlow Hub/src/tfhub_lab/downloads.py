"""Download helpers for external datasets used in the notebook."""

from __future__ import annotations

from pathlib import Path

import requests

QUORA_TRAIN_URLS = [
    "https://archive.org/download/fine-tune-bert-tensorflow-train.csv/train.csv.zip",
    # Zenodo record 6462718 lists the file `quora_train.csv.zip`; this is the standard direct-download URL shape.
    "https://zenodo.org/records/6462718/files/quora_train.csv.zip?download=1",
]


def download_file_with_fallback(
    urls: list[str],
    destination: str | Path,
    timeout: int = 60,
    chunk_size: int = 1024 * 1024,
) -> Path:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    last_error: Exception | None = None

    for url in urls:
        try:
            with requests.get(url, stream=True, timeout=timeout) as response:
                response.raise_for_status()
                with destination.open("wb") as file_obj:
                    for chunk in response.iter_content(chunk_size=chunk_size):
                        if chunk:
                            file_obj.write(chunk)
            return destination
        except requests.RequestException as exc:
            last_error = exc

    raise RuntimeError(
        "All dataset download sources failed. Last error: "
        f"{last_error}"
    ) from last_error


def download_quora_train_zip(raw_dir: str | Path) -> Path:
    raw_dir = Path(raw_dir)
    zip_path = raw_dir / "train.csv.zip"
    if zip_path.exists():
        return zip_path
    return download_file_with_fallback(QUORA_TRAIN_URLS, zip_path)

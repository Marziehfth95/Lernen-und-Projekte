"""Dataset helpers for the TF Hub NLP lab."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import tensorflow as tf
from sklearn.model_selection import train_test_split

from tfhub_lab.config import TARGET_COLUMN, TEXT_COLUMN


def load_dataset(csv_path: str | Path, sample_size: int | None = None) -> pd.DataFrame:
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(
            f"Dataset not found: {path}. Download train.csv from the Quora Insincere Questions dataset "
            "and place it under data/raw/."
        )

    frame = pd.read_csv(path)
    missing_columns = {TEXT_COLUMN, TARGET_COLUMN} - set(frame.columns)
    if missing_columns:
        raise ValueError(
            f"Dataset must contain columns {TEXT_COLUMN!r} and {TARGET_COLUMN!r}; "
            f"missing: {sorted(missing_columns)}"
        )

    frame = frame[[TEXT_COLUMN, TARGET_COLUMN]].dropna().copy()
    frame[TEXT_COLUMN] = frame[TEXT_COLUMN].astype(str)
    frame[TARGET_COLUMN] = frame[TARGET_COLUMN].astype("int32")

    if sample_size is not None:
        frame = frame.sample(n=min(sample_size, len(frame)), random_state=42)

    return frame.reset_index(drop=True)


def split_dataset(
    frame: pd.DataFrame,
    test_size: float = 0.2,
    validation_size: float = 0.1,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    train_frame, test_frame = train_test_split(
        frame,
        test_size=test_size,
        stratify=frame[TARGET_COLUMN],
        random_state=42,
    )

    validation_ratio = validation_size / (1.0 - test_size)
    train_frame, validation_frame = train_test_split(
        train_frame,
        test_size=validation_ratio,
        stratify=train_frame[TARGET_COLUMN],
        random_state=42,
    )

    return (
        train_frame.reset_index(drop=True),
        validation_frame.reset_index(drop=True),
        test_frame.reset_index(drop=True),
    )


def to_tf_dataset(frame: pd.DataFrame, batch_size: int, shuffle: bool) -> tf.data.Dataset:
    dataset = tf.data.Dataset.from_tensor_slices(
        (
            frame[TEXT_COLUMN].values,
            frame[TARGET_COLUMN].values,
        )
    )

    if shuffle:
        dataset = dataset.shuffle(len(frame), seed=42, reshuffle_each_iteration=True)

    return dataset.batch(batch_size).prefetch(tf.data.AUTOTUNE)

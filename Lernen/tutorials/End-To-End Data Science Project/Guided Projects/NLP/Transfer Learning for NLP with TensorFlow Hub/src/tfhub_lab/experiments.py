"""Reusable experiment helpers for TF Hub text-classification runs."""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import tensorflow as tf
import tensorflow_hub as hub
import tf_keras as keras

from tfhub_lab.config import (
    EXPERIMENT_SPECS,
    TARGET_COLUMN,
    TEXT_COLUMN,
    ExperimentSpec,
)


class EpochDots(keras.callbacks.Callback):
    """Small notebook-friendly progress callback similar to tensorflow_docs."""

    def on_epoch_end(self, epoch: int, logs: dict | None = None) -> None:
        if epoch % 20 == 0:
            print("")
        print(".", end="", flush=True)


def _adam_optimizer(learning_rate: float) -> keras.optimizers.Optimizer:
    """Prefer the legacy Adam optimizer on Apple Silicon for better performance."""

    legacy_optimizers = getattr(keras.optimizers, "legacy", None)
    if legacy_optimizers is not None and hasattr(legacy_optimizers, "Adam"):
        return legacy_optimizers.Adam(learning_rate=learning_rate)
    return keras.optimizers.Adam(learning_rate=learning_rate)


def build_sequential_text_model(
    module_url: str,
    embed_size: int,
    name: str,
    trainable: bool = False,
    learning_rate: float = 1e-4,
) -> keras.Model:
    """Build a Sequential TF Hub text-classification model."""

    hub_layer = hub.KerasLayer(
        module_url,
        input_shape=[],
        output_shape=[embed_size],
        dtype=tf.string,
        trainable=trainable,
        name="embedding",
    )

    model = keras.Sequential(
        [
            hub_layer,
            keras.layers.Dense(256, activation="relu"),
            keras.layers.Dense(64, activation="relu"),
            keras.layers.Dense(1, activation="sigmoid"),
        ],
        name=name,
    )

    model.compile(
        optimizer=_adam_optimizer(learning_rate),
        loss=keras.losses.BinaryCrossentropy(),
        metrics=[keras.metrics.BinaryAccuracy(name="accuracy")],
    )
    return model


def get_experiment_spec(experiment_key: str) -> ExperimentSpec:
    """Return one of the configured TF Hub experiment presets."""

    try:
        return EXPERIMENT_SPECS[experiment_key]
    except KeyError as exc:
        available = ", ".join(sorted(EXPERIMENT_SPECS))
        raise ValueError(
            f"Unknown experiment {experiment_key!r}. Available experiments: {available}"
        ) from exc


def train_and_evaluate_model(
    module_url: str,
    embed_size: int,
    name: str,
    train_df: pd.DataFrame,
    valid_df: pd.DataFrame,
    trainable: bool = False,
    epochs: int = 100,
    batch_size: int = 32,
    learning_rate: float = 1e-4,
    log_dir: str | Path = "logs",
    text_column: str = TEXT_COLUMN,
    target_column: str = TARGET_COLUMN,
    show_summary: bool = False,
    verbose: int = 0,
) -> tuple[keras.Model, keras.callbacks.History, dict[str, float]]:
    """Train a TF Hub model on dataframe-based text input and evaluate on validation data."""

    run_log_dir = Path(log_dir) / name
    run_log_dir.mkdir(parents=True, exist_ok=True)

    model = build_sequential_text_model(
        module_url=module_url,
        embed_size=embed_size,
        name=name,
        trainable=trainable,
        learning_rate=learning_rate,
    )
    if show_summary:
        model.summary()

    callbacks: list[keras.callbacks.Callback] = [
        keras.callbacks.EarlyStopping(
            monitor="val_accuracy",
            patience=2,
            mode="max",
            restore_best_weights=True,
        ),
        keras.callbacks.TensorBoard(log_dir=str(run_log_dir)),
    ]
    if verbose == 0:
        callbacks.insert(0, EpochDots())

    history = model.fit(
        train_df[text_column],
        train_df[target_column],
        epochs=epochs,
        batch_size=batch_size,
        validation_data=(valid_df[text_column], valid_df[target_column]),
        callbacks=callbacks,
        verbose=verbose,
    )

    evaluation = model.evaluate(
        valid_df[text_column],
        valid_df[target_column],
        return_dict=True,
        verbose=0,
    )

    return model, history, evaluation


def train_named_experiment(
    experiment_key: str,
    train_df: pd.DataFrame,
    valid_df: pd.DataFrame,
    histories: dict[str, keras.callbacks.History] | None = None,
    *,
    epochs: int = 10,
    batch_size: int = 32,
    learning_rate: float = 1e-4,
    log_dir: str | Path = "logs",
    trainable: bool | None = None,
    show_summary: bool = True,
    verbose: int = 1,
) -> tuple[keras.Model, keras.callbacks.History, dict[str, float]]:
    """Train a configured experiment and optionally add its history to a mapping."""

    spec = get_experiment_spec(experiment_key)
    model, history, evaluation = train_and_evaluate_model(
        module_url=spec.module_url,
        embed_size=spec.embed_size,
        name=spec.name,
        train_df=train_df,
        valid_df=valid_df,
        trainable=spec.trainable if trainable is None else trainable,
        epochs=epochs,
        batch_size=batch_size,
        learning_rate=learning_rate,
        log_dir=log_dir,
        show_summary=show_summary,
        verbose=verbose,
    )

    if histories is not None:
        histories[spec.key] = history

    return model, history, evaluation


def plot_histories(
    histories: dict[str, keras.callbacks.History],
    metric: str,
    *,
    figsize: tuple[int, int] = (12, 8),
    title: str | None = None,
) -> tuple[plt.Figure, plt.Axes]:
    """Plot training and validation curves for one metric across experiments."""

    if not histories:
        raise ValueError("No training histories available to plot.")

    figure, axes = plt.subplots(figsize=figsize)
    plotted_lines = 0

    for name, history in histories.items():
        values = history.history
        if metric in values:
            axes.plot(values[metric], label=f"{name} - train")
            plotted_lines += 1

        validation_metric = f"val_{metric}"
        if validation_metric in values:
            axes.plot(
                values[validation_metric],
                linestyle="--",
                label=f"{name} - validation",
            )
            plotted_lines += 1

    if plotted_lines == 0:
        available_metrics = sorted(
            {
                history_metric
                for history in histories.values()
                for history_metric in history.history
            }
        )
        raise ValueError(
            f"Metric {metric!r} is unavailable. Available metrics: {available_metrics}"
        )

    axes.set_xlabel("Epochs")
    axes.set_ylabel(metric.replace("_", " ").title())
    axes.set_title(title or f"{metric.title()} Curves for Models")
    axes.legend(bbox_to_anchor=(1.0, 1.0), loc="upper left")
    axes.grid(alpha=0.25)
    figure.tight_layout()
    return figure, axes


def plot_accuracy_and_loss(
    histories: dict[str, keras.callbacks.History],
    *,
    figsize: tuple[int, int] = (12, 8),
) -> tuple[tuple[plt.Figure, plt.Axes], tuple[plt.Figure, plt.Axes]]:
    """Plot accuracy and loss curves using the same style for all experiments."""

    accuracy_plot = plot_histories(
        histories,
        "accuracy",
        figsize=figsize,
        title="Accuracy Curves for Models",
    )
    loss_plot = plot_histories(
        histories,
        "loss",
        figsize=figsize,
        title="Loss Curves for Models",
    )
    return accuracy_plot, loss_plot

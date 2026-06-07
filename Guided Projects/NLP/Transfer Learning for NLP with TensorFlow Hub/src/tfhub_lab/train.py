"""CLI for training TF Hub text classifiers on the Quora dataset."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import tf_keras as keras

from tfhub_lab.data import load_dataset, split_dataset, to_tf_dataset
from tfhub_lab.modeling import build_model, get_model_spec


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-path", required=True, help="Path to train.csv")
    parser.add_argument("--model-name", default="nnlm_50d", help="Model key from config.py")
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--sample-size", type=int, default=None)
    parser.add_argument("--learning-rate", type=float, default=1e-3)
    parser.add_argument("--output-dir", default="/workspace/artifacts")
    parser.add_argument("--log-dir", default="/workspace/logs")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    run_dir = Path(args.output_dir) / f"{args.model_name}-{timestamp}"
    tb_dir = Path(args.log_dir) / f"{args.model_name}-{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=True)
    tb_dir.mkdir(parents=True, exist_ok=True)

    frame = load_dataset(args.data_path, sample_size=args.sample_size)
    train_frame, validation_frame, test_frame = split_dataset(frame)

    train_ds = to_tf_dataset(train_frame, batch_size=args.batch_size, shuffle=True)
    validation_ds = to_tf_dataset(validation_frame, batch_size=args.batch_size, shuffle=False)
    test_ds = to_tf_dataset(test_frame, batch_size=args.batch_size, shuffle=False)

    spec = get_model_spec(args.model_name)
    model = build_model(spec, learning_rate=args.learning_rate)

    callbacks = [
        keras.callbacks.EarlyStopping(
            monitor="val_auc",
            patience=2,
            mode="max",
            restore_best_weights=True,
        ),
        keras.callbacks.TensorBoard(log_dir=str(tb_dir)),
    ]

    history = model.fit(
        train_ds,
        validation_data=validation_ds,
        epochs=args.epochs,
        callbacks=callbacks,
        verbose=1,
    )

    evaluation = model.evaluate(test_ds, return_dict=True, verbose=0)

    model_path = run_dir / "model.keras"
    history_path = run_dir / "history.json"
    metrics_path = run_dir / "metrics.json"

    model.save(model_path)
    history_path.write_text(json.dumps(history.history, indent=2))
    metrics_path.write_text(json.dumps(evaluation, indent=2))

    print(f"Saved model to: {model_path}")
    print(f"Saved history to: {history_path}")
    print(f"Saved metrics to: {metrics_path}")
    print(json.dumps(evaluation, indent=2))


if __name__ == "__main__":
    main()

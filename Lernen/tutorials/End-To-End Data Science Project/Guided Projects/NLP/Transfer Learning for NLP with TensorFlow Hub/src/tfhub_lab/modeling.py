"""Model construction utilities for the TF Hub NLP lab."""

from __future__ import annotations

import tensorflow as tf
import tensorflow_hub as hub
import tf_keras as keras

from tfhub_lab.config import MODEL_SPECS, ModelSpec


def get_model_spec(model_name: str) -> ModelSpec:
    try:
        return MODEL_SPECS[model_name]
    except KeyError as exc:
        available = ", ".join(sorted(MODEL_SPECS))
        raise ValueError(f"Unknown model {model_name!r}. Available models: {available}") from exc


def build_model(spec: ModelSpec, learning_rate: float = 1e-3) -> keras.Model:
    text_input = keras.layers.Input(shape=(), dtype=tf.string, name="text")
    embedding = hub.KerasLayer(
        spec.handle,
        input_shape=[],
        dtype=tf.string,
        trainable=spec.trainable,
        name="embedding",
    )(text_input)
    x = keras.layers.Dense(spec.hidden_units, activation="relu")(embedding)
    x = keras.layers.Dropout(spec.dropout)(x)
    output = keras.layers.Dense(1, activation="sigmoid", name="prediction")(x)

    model = keras.Model(inputs=text_input, outputs=output, name=spec.name)
    model.compile(
        optimizer=keras.optimizers.Adam(learning_rate=learning_rate),
        loss=keras.losses.BinaryCrossentropy(),
        metrics=[
            keras.metrics.BinaryAccuracy(name="accuracy"),
            keras.metrics.Precision(name="precision"),
            keras.metrics.Recall(name="recall"),
            keras.metrics.AUC(name="auc"),
        ],
    )
    return model

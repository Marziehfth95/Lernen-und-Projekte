"""Project configuration for the TF Hub NLP lab."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelSpec:
    name: str
    handle: str
    trainable: bool
    hidden_units: int = 64
    dropout: float = 0.2


@dataclass(frozen=True)
class ExperimentSpec:
    key: str
    name: str
    module_url: str
    embed_size: int
    trainable: bool = False


MODEL_SPECS = {
    "swivel_20d": ModelSpec(
        name="swivel_20d",
        handle="https://tfhub.dev/google/tf2-preview/gnews-swivel-20dim/1",
        trainable=False,
    ),
    "nnlm_50d": ModelSpec(
        name="nnlm_50d",
        handle="https://tfhub.dev/google/nnlm-en-dim50/2",
        trainable=False,
    ),
    "nnlm_128d": ModelSpec(
        name="nnlm_128d",
        handle="https://tfhub.dev/google/nnlm-en-dim128/2",
        trainable=False,
    ),
    "nnlm_50d_finetune": ModelSpec(
        name="nnlm_50d_finetune",
        handle="https://tfhub.dev/google/nnlm-en-dim50/2",
        trainable=True,
    ),
}

EXPERIMENT_SPECS = {
    "gnews-swivel-20dim/1": ExperimentSpec(
        key="gnews-swivel-20dim/1",
        name="gnews-swivel-20dim",
        module_url="https://tfhub.dev/google/tf2-preview/gnews-swivel-20dim/1",
        embed_size=20,
    ),
    "gnews-swivel-20dim/1-finetuned": ExperimentSpec(
        key="gnews-swivel-20dim/1-finetuned",
        name="gnews-swivel-20dim-finetuned",
        module_url="https://tfhub.dev/google/tf2-preview/gnews-swivel-20dim/1",
        embed_size=20,
        trainable=True,
    ),
    "nnlm-en-dim50/1": ExperimentSpec(
        key="nnlm-en-dim50/1",
        name="nnlm-en-dim50",
        module_url="https://tfhub.dev/google/tf2-preview/nnlm-en-dim50/1",
        embed_size=50,
    ),
    "nnlm-en-dim128/1": ExperimentSpec(
        key="nnlm-en-dim128/1",
        name="nnlm-en-dim128",
        module_url="https://tfhub.dev/google/tf2-preview/nnlm-en-dim128/1",
        embed_size=128,
    ),
    "universal-sentence-encoder/4": ExperimentSpec(
        key="universal-sentence-encoder/4",
        name="universal-sentence-encoder",
        module_url="https://tfhub.dev/google/universal-sentence-encoder/4",
        embed_size=512,
    ),
    "universal-sentence-encoder-large/5": ExperimentSpec(
        key="universal-sentence-encoder-large/5",
        name="universal-sentence-encoder-large",
        module_url="https://tfhub.dev/google/universal-sentence-encoder-large/5",
        embed_size=512,
    ),
}

TEXT_COLUMN = "question_text"
TARGET_COLUMN = "target"

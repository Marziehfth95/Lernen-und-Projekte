COMPOSE = docker compose

.PHONY: build up down shell notebook tensorboard train local-setup help

help:
	@echo "Targets:"
	@echo "  build        Build the Docker image"
	@echo "  up           Start the workspace container in the background"
	@echo "  down         Stop the workspace container"
	@echo "  shell        Open a shell inside the running container"
	@echo "  notebook     Start Jupyter Lab on http://localhost:8888"
	@echo "  tensorboard  Start TensorBoard on http://localhost:6006"
	@echo "  train        Train the default TF Hub model"
	@echo "  local-setup  Create a local .venv for Void notebook execution"

build:
	$(COMPOSE) build

up:
	$(COMPOSE) up -d

down:
	$(COMPOSE) down

shell:
	$(COMPOSE) exec workspace bash

notebook:
	$(COMPOSE) exec workspace jupyter lab --ip=0.0.0.0 --port=8888 --no-browser --allow-root --NotebookApp.token=''

tensorboard:
	$(COMPOSE) exec workspace tensorboard --logdir /workspace/logs --host 0.0.0.0 --port 6006

train:
	$(COMPOSE) exec workspace python -m tfhub_lab.train --data-path /workspace/data/raw/train.csv --model-name nnlm_50d

local-setup:
	python3.11 -m venv .venv
	. .venv/bin/activate && python -m pip install --upgrade pip
	. .venv/bin/activate && python -m pip install -r requirements.txt
	. .venv/bin/activate && python -m pip install -e .
	. .venv/bin/activate && python -m ipykernel install --user --name coursera-tfhub-local --display-name "Coursera TF Hub (Local)"

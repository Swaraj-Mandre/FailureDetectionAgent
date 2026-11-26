"""Puts QueryMend online on Modal (modal.com), with its own Ollama and the same two models.

    modal serve deploy/modal_app.py     a temporary address, for testing
    modal deploy deploy/modal_app.py    the lasting address

Everything runs in one container: Ollama, qwen2.5:3b, nomic-embed-text and the web app in online
mode (QM_PUBLIC=1). The agent still calls nothing outside that container and needs no API key.

The container stops a few minutes after the last visitor and starts again on the next visit, which
takes about a minute. Conversations and uploaded shops live in the container, so they are gone
after it stops. The sample shop, its saved words and the evaluation results are built into the image.
"""
import os
import subprocess
import time
import urllib.request
from pathlib import Path

import modal

REPO = Path(__file__).resolve().parent.parent
OLLAMA_VERSION = "0.40.6"   # the same version we use on Windows
WORKSPACE = "swaraj-mandre"
LABEL = "querymend"
# a GPU needs a payment method on the Modal account. Without one the model runs on 4 CPU cores (slower).
GPU = os.environ.get("QM_MODAL_GPU", "")   # for example T4
# the lasting and the temporary address, with and without the region Modal may put in them
HOSTS = ",".join(f"{WORKSPACE}--{LABEL}{dev}{region}.modal.run" for dev in ("", "-dev") for region in ("", ".us-east"))

OLLAMA_ENV = {"OLLAMA_MODELS": "/models", "OLLAMA_HOST": "127.0.0.1:11434", "OLLAMA_KEEP_ALIVE": "-1",
              "OLLAMA_MAX_LOADED_MODELS": "2"}
WITH_OLLAMA = "ollama serve > /tmp/ollama.log 2>&1 & PID=$!; sleep 6; {}; kill $PID"

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("curl", "ca-certificates", "zstd")
    .env(OLLAMA_ENV)
    .run_commands(f"curl -fsSL https://ollama.com/install.sh | OLLAMA_VERSION={OLLAMA_VERSION} sh")
    .run_commands(WITH_OLLAMA.format("ollama pull qwen2.5:3b && ollama pull nomic-embed-text"))
    .pip_install_from_requirements(str(REPO / "requirements.txt"))
    # the files git keeps, nothing from our own computer's run-time state
    .add_local_dir(REPO, "/app", copy=True,
                   ignore=["**/__pycache__", "**/*.pyc", "**/*.db", "**/*.db-*", "**/*.sqlite", "ta2/state", "ta2/state/**", ".git", ".git/**",
                           "frontend/node_modules", "frontend/node_modules/**", "frontend/src/**", "presentation", "report", "docs", "deploy"])
    # the sample shop, its saved words and both error guides indexed, so the first question is quick
    .run_commands(WITH_OLLAMA.format(
        "cd /app && python data/make_shop_db.py && python ta2/seed_store.py && cd ta2 && "
        "python -c 'import rag, config; rag.get_vectorstore(); rag.get_vectorstore(guide=config.GENERAL_GUIDE_FILE)'"))
)

app = modal.App(LABEL, image=image)


def wait_for(url, seconds):
    end = time.time() + seconds
    while time.time() < end:
        try:
            urllib.request.urlopen(url, timeout=2)
            return
        except OSError:
            time.sleep(0.5)
    raise RuntimeError(f"{url} did not answer in {seconds} s")


@app.function(gpu=GPU or None, cpu=2 if GPU else 4, memory=8192, max_containers=1, scaledown_window=300, timeout=3600)
@modal.concurrent(max_inputs=50)
@modal.web_server(8000, startup_timeout=300, label=LABEL)
def web():
    env = {**os.environ, **OLLAMA_ENV}
    subprocess.Popen(["ollama", "serve"], env=env)
    wait_for("http://127.0.0.1:11434/api/version", 60)
    # load the chat model now, so the first visitor does not wait for it
    urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:11434/api/generate",
                                                  data=b'{"model": "qwen2.5:3b", "prompt": "", "keep_alive": -1}'), timeout=120)
    subprocess.Popen(["python", "-X", "utf8", "ta2/server.py"], cwd="/app",
                     env={**env, "QM_PUBLIC": "1", "QM_HOSTS": HOSTS, "PORT": "8000", "PYTHONDONTWRITEBYTECODE": "1"})

"""Shared GPU image for arbitrary notebook projects, independent of local data."""
from pathlib import Path
import modal

PROJECT = Path(__file__).resolve().parent
image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install("torch==2.5.1", "torchvision==0.20.1", index_url="https://download.pytorch.org/whl/cu124")
    .pip_install("numpy==2.1.3", "pandas==2.2.3", "Pillow==11.1.0",
                 "nbformat==5.10.4", "nbclient==0.10.2", "ipykernel==6.30.1")
    .env({"CUBLAS_WORKSPACE_CONFIG": ":4096:8", "PYTHONUNBUFFERED": "1"})
)

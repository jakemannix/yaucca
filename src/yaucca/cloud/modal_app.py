"""Modal app definition for yaucca cloud server.

Deploy with: modal deploy src/yaucca/cloud/modal_app.py
Develop with: modal serve src/yaucca/cloud/modal_app.py
"""

import modal

app = modal.App("yaucca")

volume = modal.Volume.from_name("yaucca-data", create_if_missing=True)

image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "fastapi>=0.115.0",
        "uvicorn>=0.30.0",
        "httpx>=0.27.0",
        "pydantic>=2.0.0",
        "pydantic-settings>=2.0.0",
        "sqlite-vec>=0.1.0",
    )
    .add_local_python_source("yaucca")
)


@app.function(
    image=image,
    volumes={"/data": volume},
    scaledown_window=300,
    secrets=[modal.Secret.from_name("yaucca-secrets")],
)
@modal.concurrent(max_inputs=10)
@modal.asgi_app()
def serve():
    """Serve the yaucca FastAPI app with SQLite on a persistent volume."""
    from yaucca.cloud.server import create_app

    return create_app(
        db_path="/data/yaucca.db",
        on_write=lambda: None,  # Block writes don't need volume commit (low freq)
        commit_fn=volume.commit,  # Embedding queue commits after each batch flush
    )

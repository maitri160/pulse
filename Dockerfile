FROM python:3.12-slim

# Copy the uv binary from its official image (same version as local dev).
COPY --from=ghcr.io/astral-sh/uv:0.13.4 /uv /bin/uv

WORKDIR /app

# Install dependencies first, in their own layer. Docker caches layers, so this
# slow step only re-runs when pyproject.toml or uv.lock change, not on every code edit.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app ./app

# Put the venv uv created on PATH so `uvicorn` etc. resolve to it.
ENV PATH="/app/.venv/bin:$PATH"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

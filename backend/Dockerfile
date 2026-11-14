FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /app
# Debian security updates the base image hasn't picked up yet (make scan <env> checks).
RUN apt-get update && apt-get upgrade -y --no-install-recommends \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Exact versions with hashes (make lock regenerates them from pyproject.toml);
# pip refuses any package that isn't listed or doesn't match its hash.
FROM base AS deps
COPY requirements.lock ./
RUN pip install --upgrade pip setuptools && pip install --require-hashes -r requirements.lock

FROM deps AS dev
COPY requirements-dev.lock ./
RUN pip install --require-hashes -r requirements-dev.lock
COPY . .
RUN chmod +x entrypoint.sh
ENTRYPOINT ["sh", "/app/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

FROM deps AS prod
COPY . .
# pip is only needed to build the image. Removing it also removes the copy of
# setuptools' pkg_resources it bundles, which scanners report as setuptools 70.3.0.
RUN chmod +x entrypoint.sh && useradd --create-home --uid 1000 app && chown -R app:app /app \
    && pip uninstall -y pip
USER app
ENTRYPOINT ["sh", "/app/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

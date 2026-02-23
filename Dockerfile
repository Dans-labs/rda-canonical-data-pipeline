FROM python:3.12.8-bookworm
LABEL authors="Eko Indarto"

ARG BUILD_DATE
ENV BUILD_DATE=$BUILD_DATE


# Combine apt-get commands to reduce layers
RUN apt-get update -y && \
    apt-get upgrade -y && \
    apt-get dist-upgrade -y && \
    apt-get install -y --no-install-recommends git curl postgresql-client && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

RUN useradd -ms /bin/bash akmi

ENV PYTHONPATH=/home/akmi/rcdp/src
ENV BASE_DIR=/home/akmi/rcdp

WORKDIR ${BASE_DIR}


# Install uv.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Copy the application into the container.


ENV APP_NAME="RDA Cannonical Data Service"
ENV PATH="/home/akmi/rcdp/.venv/bin:$PATH"
# Copy the application into the container.
COPY src ./src
COPY resources ./resources
COPY pyproject.toml .
COPY README.md .
COPY uv.lock .


# ensure required packages and add PostgreSQL APT repository then install postgresql-client-16
RUN apt-get update -y \
    && apt-get install -y --no-install-recommends ca-certificates wget gnupg lsb-release \
    && wget -qO - https://www.postgresql.org/media/keys/ACCC4CF8.asc | apt-key add - \
    && echo "deb http://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update -y \
    && apt-get install -y --no-install-recommends postgresql-client-16 \
    && apt-get clean && rm -rf /var/lib/apt/lists/*


# make uv venv idempotent (clear existing venv if present)
RUN uv venv .venv --clear
# Install dependencies

RUN uv sync --frozen --no-cache

# Run the application.
CMD ["python", "-m", "src.cannonical_data_pipeline.main"]

#CMD ["tail", "-f", "/dev/null"]
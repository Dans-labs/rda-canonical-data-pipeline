FROM python:3.12.8-bookworm
LABEL authors="Eko Indarto"

ARG BUILD_DATE
ENV BUILD_DATE=$BUILD_DATE


# Combine apt-get commands to reduce layers
RUN apt-get update -y && \
    apt-get upgrade -y && \
    apt-get dist-upgrade -y && \
    apt-get install -y --no-install-recommends git curl && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

RUN useradd -ms /bin/bash akmi

ENV PYTHONPATH=/home/akmi/cdp/src
ENV BASE_DIR=/home/akmi/cdp

WORKDIR ${BASE_DIR}


# Install uv.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Copy the application into the container.


# Create and activate virtual environment
RUN python -m venv .venv
ENV APP_NAME="RDA Cannonical Data Service"
ENV PATH="/home/akmi/cdp/.venv/bin:$PATH"
# Copy the application into the container.
COPY src ./src
COPY resources ./resources
COPY pyproject.toml .
COPY README.md .
COPY uv.lock .


RUN uv venv .venv
# Install dependencies

RUN uv sync --frozen --no-cache

# Run the application.
CMD ["python", "-m", "src.main"]

#CMD ["tail", "-f", "/dev/null"]
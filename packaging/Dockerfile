FROM debian:bookworm-slim@sha256:7ff6ba31527b9e336162fa061746c366c4b2032f4d2cff2078bea43f78efe3f8
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-tk python3-venv libpython3.11 binutils ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /build/requirements.txt
RUN python3 -m venv /opt/portable-build \
    && /opt/portable-build/bin/pip install --no-cache-dir -r /build/requirements.txt
ENV PATH="/opt/portable-build/bin:$PATH" PYTHONDONTWRITEBYTECODE=1
WORKDIR /src

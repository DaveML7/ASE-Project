# One-shot provisioning job: generates the secrets and the TLS certificates on
# first start. Nothing secret is stored in any image or in the repository.
# /runtime is owned by the unprivileged user so that a fresh named volume
# inherits that ownership.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

# Pick up Debian security fixes published after the base image was built.
RUN apt-get update \
    && apt-get install -y --no-install-recommends --only-upgrade zlib1g \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

RUN useradd --uid 10001 --create-home appuser \
    && mkdir /runtime \
    && chown appuser /runtime
COPY init.py /opt/init.py
COPY provisioning /opt/provisioning

USER appuser
ENTRYPOINT ["python", "/opt/init.py"]

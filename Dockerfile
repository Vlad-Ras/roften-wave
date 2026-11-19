FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

# OS dependencies change infrequently, so keep them above Python source layers.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/roften-wave

# Installing third-party packages before copying Python source preserves this
# Docker layer whenever only application code or static assets change.
# The list is read from pyproject.toml itself: no duplicate dependency list.
COPY pyproject.toml ./
RUN python -c 'import subprocess, sys, tomllib; from pathlib import Path; config = tomllib.loads(Path("pyproject.toml").read_text()); deps = config["project"]["dependencies"]; build = config["build-system"]["requires"]; subprocess.check_call([sys.executable, "-m", "pip", "install", "--no-cache-dir", *deps, *build])'

COPY roften_wave ./roften_wave
# Install only the tiny local application package; dependencies already exist.
RUN python -m pip install --no-cache-dir --no-build-isolation --no-deps .

RUN useradd -r -u 10001 -m wave && mkdir /data && chown wave:wave /data
USER wave
EXPOSE 8787
CMD ["uvicorn", "roften_wave.web:app", "--host", "0.0.0.0", "--port", "8787", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "127.0.0.1"]

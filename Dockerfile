FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/roften-wave
COPY pyproject.toml ./
COPY roften_wave ./roften_wave
RUN pip install --no-cache-dir .
RUN useradd -r -u 10001 -m wave && mkdir /data && chown wave:wave /data
USER wave
EXPOSE 8787
CMD ["uvicorn", "roften_wave.web:app", "--host", "0.0.0.0", "--port", "8787", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "127.0.0.1"]

FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
# Run as the 10km image's user so SQLite can create the WAL -shm file on the shared volume.
RUN useradd -u 1000 -m mtv
COPY mtv /opt/mtv/mtv
ENV PYTHONPATH=/opt/mtv PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER mtv
EXPOSE 8710
ENTRYPOINT ["python3", "-m", "mtv"]

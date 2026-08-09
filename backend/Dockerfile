# RescueNet AI backend.
#
# Python 3.11 here even though the code targets 3.10 — it runs unchanged on both, and
# the deployed image may as well have the newer runtime.
FROM python:3.11-slim

# Pillow and imagehash ship manylinux wheels, so no build toolchain is needed.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first, so a code change does not re-resolve the whole tree.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY seed ./seed

# Uploaded media. On a free-tier host this is ephemeral — see the README; object
# storage is the fix, not a larger disk.
RUN mkdir -p /app/media

EXPOSE 8000

# The platform supplies $PORT; 8000 is the local default.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]

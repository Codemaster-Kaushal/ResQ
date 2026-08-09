FROM python:3.11-slim

LABEL maintainer="RescueNet AI Team"
LABEL description="RescueNet AI Engine — offline-first disaster triage"

# Create non-root user
RUN useradd -m -u 1000 rescuenet

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY --chown=rescuenet:rescuenet . .

USER rescuenet

# Expose API port
EXPOSE 8000

# Default: production-like startup
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

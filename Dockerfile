# Dockerfile for the B.Tech Timetable Generator web service.
# Builds a small image that serves the FastAPI app with uvicorn.

FROM python:3.11-slim

# Avoid .pyc files, unbuffered logs (better for container logs).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the source.
COPY . .

# Most PaaS hosts inject $PORT; default to 8000 for local runs.
ENV PORT=8000
EXPOSE 8000

# Shell form so $PORT expands at runtime.
CMD uvicorn app:app --host 0.0.0.0 --port ${PORT}

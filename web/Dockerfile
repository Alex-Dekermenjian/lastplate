# Development image. Each service builds from its own folder only.
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY . .
RUN useradd --create-home lastplate
USER lastplate

EXPOSE 8000
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]

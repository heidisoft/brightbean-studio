FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc curl ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js for Tailwind build
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Build Tailwind CSS
RUN cd theme/static_src && npm ci && npm run build

# Collect static files. INBOX_AI_ENABLED is forced on here (independent of
# the runtime env var) so apps.inbox_ai is registered and its assets land in
# the manifest — otherwise enabling the feature at runtime 404s/500s on
# inbox_ai/reply.css because whitenoise's manifest is immutable after build.
RUN DJANGO_SETTINGS_MODULE=config.settings.production \
    SECRET_KEY=build-placeholder \
    DATABASE_URL=sqlite:///tmp/build.db \
    INBOX_AI_ENABLED=True \
    python manage.py collectstatic --noinput

EXPOSE 8000

CMD gunicorn config.wsgi:application --bind 0.0.0.0:${PORT:-8000} --workers 2 --threads 2

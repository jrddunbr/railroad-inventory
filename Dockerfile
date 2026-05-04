FROM python:3.13-slim

WORKDIR /app

ARG APP_GIT_COMMIT=""

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_GIT_COMMIT=${APP_GIT_COMMIT}

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY . .

LABEL org.opencontainers.image.revision="${APP_GIT_COMMIT}"

EXPOSE 5000

CMD ["gunicorn", "-b", "0.0.0.0:5000", "wsgi:app"]

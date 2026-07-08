FROM python:3.14-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY dist/thread-search-public /data

RUN pip install --no-cache-dir .

ENV THREAD_SEARCH_DB=/data/thread-search.sqlite
ENV THREAD_SEARCH_ARTIFACT_MANIFEST=/data/manifest.json
ENV THREAD_SEARCH_PUBLIC_CONTACT=
ENV THREAD_SEARCH_REMOVAL_REQUEST_URL=
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/healthz', timeout=4).read()"

CMD ["sh", "-c", "thread-search serve --host 0.0.0.0 --port 8765 --db \"${THREAD_SEARCH_DB:-$PLANQUEST_DB}\" --require-launch-ready --require-artifact-manifest --artifact-manifest \"${THREAD_SEARCH_ARTIFACT_MANIFEST:-$PLANQUEST_ARTIFACT_MANIFEST}\" --public-contact \"${THREAD_SEARCH_PUBLIC_CONTACT:-$PLANQUEST_PUBLIC_CONTACT}\" --removal-request-url \"${THREAD_SEARCH_REMOVAL_REQUEST_URL:-$PLANQUEST_REMOVAL_REQUEST_URL}\" --probe Soviet --probe Cuba"]

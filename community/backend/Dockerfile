FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

# Build deps
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e "."

COPY app ./app
COPY tests ./tests

# Independent data volume (community DB + packages)
VOLUME ["/app/.data"]
ENV COMMUNITY_DATA_DIR=/app/.data

EXPOSE 8001
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8001/status',timeout=4).status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]

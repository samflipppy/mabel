FROM python:3.12-slim

WORKDIR /app

COPY packages/verticals /tmp/verticals
COPY apps/api /app

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir /tmp/verticals \
    && pip install --no-cache-dir .

EXPOSE 8000

CMD ["uvicorn", "mabel.app:app", "--host", "0.0.0.0", "--port", "8000"]

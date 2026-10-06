FROM python:3.12-alpine AS builder
WORKDIR /build
RUN pip install --no-cache-dir uv==0.9.5
COPY requirements.txt .
RUN uv pip install --python /usr/local/bin/python --target /deps --compile-bytecode -r requirements.txt

FROM python:3.12-alpine AS base
WORKDIR /app
COPY --from=builder /deps /usr/local/lib/python3.12/site-packages
RUN addgroup -S app && adduser -S -G app -u 10001 app
USER app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
EXPOSE 8080

FROM base AS edge
COPY edge.py .
CMD ["python", "-m", "uvicorn", "edge:app", "--host", "0.0.0.0", "--port", "8080"]

FROM base AS function
COPY domain.py runtime.py schema.sql seed.json index.html register.py function-specs.json .
CMD ["python", "-m", "uvicorn", "runtime:app", "--host", "0.0.0.0", "--port", "8080"]

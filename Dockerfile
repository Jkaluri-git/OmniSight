FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV OMNISIGHT_RUNS_DIR=/data/runs
VOLUME /data
EXPOSE 8000
CMD ["python", "-m", "omnisight", "serve", "--port", "8000"]

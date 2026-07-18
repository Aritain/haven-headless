FROM python:3.12-slim-bookworm

# The Python dashboard starts Hurricane's Java headless client for scout jobs.
RUN apt-get update \
    && apt-get install --no-install-recommends -y openjdk-17-jre-headless xauth xvfb \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY Scripts/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY Scripts/ ./
COPY Hurricane/ /opt/hurricane/

EXPOSE 5000

CMD ["python3", "app.py"]

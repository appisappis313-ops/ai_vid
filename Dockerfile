FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-tk \
    xvfb \
    scrot \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["xvfb-run", "-a", "-s", "-screen 0 1024x768x24", "python", "mino_chat_bot.py"]

# легкий python
FROM python:3.11-slim

# системные пакеты (для pandas/pyarrow иногда нужен gcc)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl && \
    rm -rf /var/lib/apt/lists/*

# рабочая директория
WORKDIR /app

# заранее ставим зависимые колёса
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# копируем код
COPY . /app

# переменные окружения Streamlit
ENV STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_SERVER_PORT=8501 \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0

EXPOSE 8501

# запускаем
CMD ["streamlit", "run", "streamlit_app.py", "--server.fileWatcherType=none"]

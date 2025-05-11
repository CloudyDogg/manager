FROM python:3.9-slim

WORKDIR /app

# Установка зависимостей
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копирование файлов проекта
COPY . .

# Создание директорий для скриншотов
RUN mkdir -p screen

# Устанавливаем переменную окружения для python, чтобы вывод был сразу виден в логах
ENV PYTHONUNBUFFERED=1

# Запуск бота
CMD ["python", "bot.py"] 
"""
Бот для Яндекс Дзена: сам придумывает тему, пишет статью и присылает
готовый текст в Telegram — публикуете вы вручную.

Полностью бесплатная схема:
  - генерация текста — Google Gemini API (бесплатный тариф)
  - публикация — Telegram Bot API (бесплатно)
  - расписание — GitHub Actions (бесплатно) или cron на своём ПК

Что нужно перед запуском:
1. pip install google-genai requests --break-system-packages
2. Получить бесплатный ключ Gemini: https://ai.google.dev (кнопка "Get API key")
3. Переменные окружения:
   GEMINI_API_KEY      — ключ API Google Gemini (бесплатный)
   TELEGRAM_BOT_TOKEN  — токен бота от @BotFather
   TELEGRAM_CHAT_ID    — ваш chat_id (узнать через @userinfobot или /getUpdates)

Запуск вручную:
   python3 zen_bot.py

Автозапуск по расписанию — см. инструкцию в конце файла (cron / GitHub Actions).
"""

import os
import json
import time
import textwrap
from datetime import datetime

from google import genai
from google.genai import errors as genai_errors
import requests

# ---------- Настройки ----------

TARGET_MIN_CHARS = 3300
TARGET_MAX_CHARS = 5700

# Общая ниша канала — задаёт тон и рамку для тем.
# Отредактируйте под свой канал (сейчас настроено под "Апгрейд" — саморазвитие).
CHANNEL_THEME = "саморазвитие, психология привычек, продуктивность, ментальное здоровье"

# Файл, где бот запоминает уже использованные темы, чтобы не повторяться
USED_TOPICS_FILE = "used_topics.json"

# Бесплатная модель Gemini с щедрым дневным лимитом
GEMINI_MODEL = "gemini-3.6-flash"

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])


def ask_gemini(prompt, max_tokens=3000, retries=5):
    for attempt in range(retries):
        try:
            resp = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config={"max_output_tokens": max_tokens},
            )
            return resp.text.strip()
        except genai_errors.ServerError as e:
            # 503 — сервер Gemini временно перегружен, ждём и пробуем снова
            wait = 15 * (attempt + 1)
            print(f"Сервер Gemini занят ({e}). Пробую снова через {wait} сек...")
            time.sleep(wait)
    raise RuntimeError("Gemini не ответил после нескольких попыток — попробуйте запустить бота позже.")


# ---------- Шаг 1. Придумать тему ----------

def load_used_topics():
    if os.path.exists(USED_TOPICS_FILE):
        with open(USED_TOPICS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_used_topic(topic):
    used = load_used_topics()
    used.append({"topic": topic, "date": datetime.now().strftime("%Y-%m-%d")})
    with open(USED_TOPICS_FILE, "w", encoding="utf-8") as f:
        json.dump(used, f, ensure_ascii=False, indent=2)


def generate_topic():
    used = load_used_topics()
    used_list = "\n".join(f"- {u['topic']}" for u in used[-30:])  # последние 30, чтобы не раздувать промпт

    prompt = f"""Придумай ОДНУ тему для статьи на Яндекс Дзен.

Ниша канала: {CHANNEL_THEME}

Требования к теме:
- конкретная, не абстрактная (не "про мотивацию", а например "почему списки дел не работают и что делать вместо них")
- интересная и кликабельная для широкой аудитории, без желтизны и кликбейта
- пока не использовалась в канале

Уже использованные темы (не повторяй их и не делай слишком похожими):
{used_list if used_list else "(пока нет)"}

Ответь ТОЛЬКО темой статьи, одной строкой, без кавычек, без пояснений."""

    topic = ask_gemini(prompt, max_tokens=300)
    return topic


# ---------- Шаг 2. Написать статью нужной длины ----------

def generate_article(topic):
    prompt = f"""Напиши статью для Яндекс Дзена на тему: «{topic}»

Требования:
- Длина СТРОГО 3300–5700 знаков с пробелами (примерно 470–815 слов). Это обязательное условие.
- Разговорный, живой стиль — как будто автор объясняет другу, без канцелярита и воды.
- Структура: цепляющий заголовок, короткое вступление (проблема/вопрос), 3–5 смысловых блоков
  с подзаголовками, короткий вывод в конце.
- Никаких списков литературы, ссылок, markdown-разметки (**, ##) — только заголовок и обычный текст
  с подзаголовками на отдельных строках.
- Без слова "итак" в начале и без канцелярских штампов ("в данной статье", "актуальность темы").
- Заголовок — с новой строки в самом начале, без слова "Заголовок:".

Ответь только готовым текстом статьи, без комментариев до или после."""

    article = ask_gemini(prompt, max_tokens=6000)
    return article


def regenerate_if_wrong_length(topic, article, attempts=2):
    """Если длина не попала в диапазон — просим переписать короче/длиннее."""
    for _ in range(attempts):
        length = len(article)
        if TARGET_MIN_CHARS <= length <= TARGET_MAX_CHARS:
            return article

        direction = "короче" if length > TARGET_MAX_CHARS else "длиннее"
        fix_prompt = f"""Вот статья на тему «{topic}»:

{article}

Текущая длина: {length} знаков. Нужно {direction}, чтобы уложиться СТРОГО в 3300–5700 знаков
с пробелами. Перепиши статью целиком с учётом этого, сохранив стиль и структуру."""

        article = ask_gemini(fix_prompt, max_tokens=6000)

    return article


# ---------- Шаг 3. Отправить в Telegram ----------

def send_to_telegram(topic, article):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    url = f"https://api.telegram.org/bot{token}/sendMessage"

    length = len(article)
    header = f"📝 Новая статья готова\nТема: {topic}\nДлина: {length} знаков\n\n"

    # Telegram режет сообщения по 4096 символов — статья может быть длиннее,
    # поэтому шлём частями.
    full_text = header + article
    chunks = textwrap.wrap(full_text, 4000, replace_whitespace=False, break_long_words=False)

    for chunk in chunks:
        resp = requests.post(url, data={"chat_id": chat_id, "text": chunk})
        resp.raise_for_status()


# ---------- Основной сценарий ----------

def main():
    print("Придумываю тему...")
    topic = generate_topic()
    print(f"Тема: {topic}")

    print("Пишу статью...")
    article = generate_article(topic)
    article = regenerate_if_wrong_length(topic, article)

    print(f"Готово. Длина: {len(article)} знаков.")

    print("Отправляю в Telegram...")
    send_to_telegram(topic, article)

    save_used_topic(topic)
    print("Готово! Проверьте Telegram.")


if __name__ == "__main__":
    main()


# ---------------------------------------------------------------------------
# АВТОЗАПУСК ПО РАСПИСАНИЮ
# ---------------------------------------------------------------------------
#
# Вариант A — cron на своём сервере/VPS:
#   1. crontab -e
#   2. Добавить строку (пример — каждый день в 9:00):
#      0 9 * * * cd /путь/к/папке && /usr/bin/python3 zen_bot.py >> log.txt 2>&1
#   3. Не забудьте прописать переменные окружения в самом crontab или в .env,
#      который скрипт подгружает перед запуском.
#
# Вариант B — GitHub Actions (не нужен свой сервер, бесплатно):
#   1. Положить этот файл и requirements.txt в репозиторий на GitHub.
#   2. В настройках репозитория: Settings → Secrets and variables → Actions
#      добавить GEMINI_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID.
#   3. Создать файл .github/workflows/daily.yml:
#
#      name: daily-article
#      on:
#        schedule:
#          - cron: '0 6 * * *'   # 9:00 по Москве (UTC+3)
#        workflow_dispatch: {}    # позволяет запустить вручную кнопкой
#      jobs:
#        run:
#          runs-on: ubuntu-latest
#          steps:
#            - uses: actions/checkout@v4
#            - uses: actions/setup-python@v5
#              with:
#                python-version: '3.11'
#            - run: pip install google-genai requests
#            - run: python3 zen_bot.py
#              env:
#                GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
#                TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
#                TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
#            - run: |
#                git config user.name "bot"
#                git config user.email "bot@users.noreply.github.com"
#                git add used_topics.json
#                git commit -m "update used topics" || echo "nothing to commit"
#                git push
#
#      Последний шаг сохраняет историю использованных тем обратно в репозиторий,
#      чтобы бот не повторялся при следующем запуске.
# ---------------------------------------------------------------------------

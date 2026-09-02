"""
Бот для Яндекс Дзена: сам придумывает тему, пишет статью и публикует её
прямо в Telegram-канал (без ручного копирования).

Полностью бесплатная схема:
  - генерация текста — Groq API (бесплатный тариф, щедрый лимит — до 14 400 запросов/день)
  - публикация — Telegram Bot API, прямо в канал (бесплатно)
  - расписание — GitHub Actions (бесплатно) или cron на своём ПК

Что нужно перед запуском:
1. pip install groq requests --break-system-packages
2. Получить бесплатный ключ Groq: https://console.groq.com/keys (регистрация бесплатная)
3. Создать Telegram-бота через @BotFather, получить токен
4. Создать Telegram-канал (или использовать существующий), добавить бота
   в канал как АДМИНИСТРАТОРА (с правом публикации сообщений)
5. Переменные окружения:
   GROQ_API_KEY        — ключ API Groq (бесплатный)
   TELEGRAM_BOT_TOKEN  — токен бота от @BotFather
   TELEGRAM_CHAT_ID    — username канала вида @my_channel (если канал публичный)
                         или числовой id канала вида -1001234567890 (если приватный)

Запуск вручную:
   python3 zen_bot.py

Автозапуск по расписанию — см. инструкцию в конце файла (cron / GitHub Actions).
"""

import os
import json
import time
from datetime import datetime

from groq import Groq
import groq as groq_errors
import requests

# ---------- Настройки ----------

TARGET_MIN_CHARS = 3300
TARGET_MAX_CHARS = 5700

# Общая ниша канала — задаёт тон и рамку для тем.
# Отредактируйте под свой канал (сейчас настроено под "Апгрейд" — саморазвитие).
CHANNEL_THEME = "саморазвитие, психология привычек, продуктивность, ментальное здоровье"

# Файл, где бот запоминает уже использованные темы, чтобы не повторяться
USED_TOPICS_FILE = "used_topics.json"

# Бесплатная модель Groq с щедрым дневным лимитом и хорошим качеством текста
GROQ_MODEL = "openai/gpt-oss-120b"

client = Groq(api_key=os.environ["GROQ_API_KEY"])


def ask_groq(prompt, max_tokens=3000, retries=5):
    for attempt in range(retries):
        try:
            resp = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_completion_tokens=max_tokens,
                timeout=120,
            )
            return resp.choices[0].message.content.strip()
        except (groq_errors.RateLimitError, groq_errors.APIStatusError, groq_errors.APIConnectionError) as e:
            status_code = getattr(e, "status_code", None)
            if status_code == 404:
                raise RuntimeError(
                    f"Модель '{GROQ_MODEL}' не найдена или недоступна на Groq. "
                    "Проверьте актуальное название модели на console.groq.com/docs/models "
                    "и обновите GROQ_MODEL в начале скрипта."
                ) from e
            wait = 20 * (attempt + 1)
            print(f"Groq занят/недоступен ({e}). Пробую снова через {wait} сек...", flush=True)
            time.sleep(wait)
    raise RuntimeError("Groq не ответил после нескольких попыток — попробуйте запустить бота позже.")


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

    topic = ask_groq(prompt, max_tokens=300)
    return topic


# ---------- Шаг 2. Написать статью нужной длины ----------

def generate_article(topic):
    prompt = f"""Ты — опытный автор популярных статей для Яндекс Дзена с миллионами прочтений.
Напиши статью на тему: «{topic}»

СТРОГИЕ ТРЕБОВАНИЯ К СТИЛЮ:
- Пиши как живой человек, а не как ИИ: короткие и средние предложения, разговорные обороты,
  живые примеры из повседневной жизни, лёгкая ирония там, где уместно.
- Каждый абзац — 2-4 предложения, не больше. Длинные "простыни" текста читатель на Дзене не читает.
- Никаких общих фраз и воды ("это важно", "многие сталкиваются с этой проблемой") — сразу конкретика,
  примеры, цифры, детали.
- Обращайся к читателю на "вы", вовлекай вопросами, но не переусердствуй.
- Не повторяй одну и ту же мысль разными словами в разных абзацах — у каждого абзаца своя новая мысль.
- Не используй канцелярит и штампы: "актуальность темы", "в данной статье", "подводя итог".

СТРУКТУРА (обязательно):
1. Заголовок — цепляющий, конкретный, без кликбейта и кавычек. Отдельной первой строкой.
2. Вступление (2-3 абзаца) — зацепи проблемой, вопросом или неожиданным фактом, из-за которого
   читатель захочет дочитать до конца.
3. Основная часть — 3-5 смысловых блоков, у каждого свой короткий подзаголовок (3-6 слов).
   В каждом блоке — конкретная мысль, раскрытая на примере или объяснении, без воды.
4. Короткий вывод (1-2 абзаца) — не пересказ статьи заново, а главная мысль и что с ней делать.

ТЕХНИЧЕСКИЕ ТРЕБОВАНИЯ:
- Длина СТРОГО 3300–5700 знаков с пробелами (примерно 470–815 слов).
- Заголовки блоков — каждый на отдельной строке, БЕЗ markdown-символов (без **, ##, -, *).
  Просто текст подзаголовка на отдельной строке.
- Между абзацами — пустая строка.
- Никаких списков литературы, ссылок, хэштегов.

Прежде чем писать, продумай: какая одна главная мысль должна остаться у читателя после прочтения?
Вся статья должна вести именно к ней.

Ответь только готовым текстом статьи, без комментариев до или после."""

    article = ask_groq(prompt, max_tokens=6000)
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

        article = ask_groq(fix_prompt, max_tokens=6000)

    return article


# ---------- Шаг 3. Опубликовать в Telegram-канал ----------

def format_article_html(article):
    """
    Превращает обычный текст статьи в HTML для Telegram:
    - первая строка (заголовок) — жирным и крупнее визуально за счёт emoji-разделителя
    - короткие строки-подзаголовки (без точки в конце, до 6 слов) — тоже жирным
    """
    lines = [l.strip() for l in article.split("\n")]
    # убираем пустые строки в начале/конце
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()

    if not lines:
        return article

    title = lines[0]
    body_lines = lines[1:]

    html_parts = [f"<b>{escape_html(title)}</b>"]

    for line in body_lines:
        if not line:
            continue
        word_count = len(line.split())
        looks_like_subheading = (
            word_count <= 6
            and not line.endswith((".", "!", "?", ","))
            and len(line) < 60
        )
        if looks_like_subheading:
            html_parts.append(f"\n<b>{escape_html(line)}</b>")
        else:
            html_parts.append(escape_html(line))

    return "\n\n".join(html_parts)


def escape_html(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def send_to_telegram(topic, article):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]  # username канала (@my_channel) или его числовой id (-100...)
    url = f"https://api.telegram.org/bot{token}/sendMessage"

    formatted = format_article_html(article)

    # Telegram режет сообщения по 4096 символов — статья может быть длиннее,
    # поэтому шлём частями по абзацам, не разрывая HTML-теги на середине.
    chunks = split_html_safely(formatted, 4000)

    for chunk in chunks:
        resp = requests.post(url, data={
            "chat_id": chat_id,
            "text": chunk,
            "parse_mode": "HTML",
        })
        if not resp.ok:
            print(f"Ошибка Telegram: {resp.status_code} {resp.text}")
        resp.raise_for_status()


def split_html_safely(text, max_len):
    """Режет текст на части по границам абзацев (\n\n), чтобы не разорвать HTML-тег пополам."""
    paragraphs = text.split("\n\n")
    chunks = []
    current = ""
    for p in paragraphs:
        candidate = (current + "\n\n" + p) if current else p
        if len(candidate) > max_len:
            if current:
                chunks.append(current)
            current = p
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


# ---------- Основной сценарий ----------

def main():
    print("Придумываю тему...", flush=True)
    topic = generate_topic()
    print(f"Тема: {topic}", flush=True)

    print("Пишу статью...", flush=True)
    article = generate_article(topic)
    article = regenerate_if_wrong_length(topic, article)

    print(f"Готово. Длина: {len(article)} знаков.", flush=True)

    print("Отправляю в Telegram...", flush=True)
    send_to_telegram(topic, article)

    save_used_topic(topic)
    print("Готово! Проверьте Telegram.", flush=True)


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
#      добавить GROQ_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID.
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
#          timeout-minutes: 15   # если что-то зависнет — job остановится сам через 15 минут
#          steps:
#            - uses: actions/checkout@v4
#            - uses: actions/setup-python@v5
#              with:
#                python-version: '3.11'
#            - run: pip install groq requests
#            - run: python3 zen_bot.py
#              env:
#                GROQ_API_KEY: ${{ secrets.GROQ_API_KEY }}
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

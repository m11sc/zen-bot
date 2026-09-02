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
import re
import time
from datetime import datetime

from groq import Groq
import groq as groq_errors
import requests

# ---------- Настройки ----------

TELEGRAM_MESSAGE_LIMIT = 4096  # жёсткий лимит Telegram на одно сообщение

# Целевая длина самого текста статьи (без HTML-тегов). Взята с запасом от лимита Telegram,
# чтобы после добавления <b>...</b> для заголовка/подзаголовков пост всё равно помещался в одно сообщение.
TARGET_MIN_CHARS = 2800
TARGET_MAX_CHARS = 3800

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

    prompt = f"""Придумай ОДНУ тему для поста в Telegram-канале о саморазвитии.

Ниша канала: {CHANNEL_THEME}

Требования к теме:
- конкретная, не абстрактная (не "про мотивацию", а например "почему списки дел не работают и что делать вместо них")
- звучит как крючок для поста в популярном канале — цепляющая, разговорная, вызывает желание узнать больше
- пока не использовалась в канале

Уже использованные темы (не повторяй их и не делай слишком похожими):
{used_list if used_list else "(пока нет)"}

Ответь ТОЛЬКО темой поста, одной строкой, без кавычек, без пояснений."""

    topic = ask_groq(prompt, max_tokens=300)
    return topic


# ---------- Шаг 2. Написать статью нужной длины ----------

def generate_article(topic):
    prompt = f"""Ты ведёшь популярный Telegram-канал о саморазвитии с сотней тысяч подписчиков —
в духе крупных каналов этой ниши (как «Психология саморазвития», «Сила слов» и похожие).
Напиши пост на тему: «{topic}»

КАК ПИШУТ ТАКИЕ КАНАЛЫ (обязательно копируй этот стиль, не пиши как формальную статью):
- Первая строка — не сухой заголовок, а цепляющий крючок: провокационное утверждение, вопрос
  в лоб или неожиданный факт. Читатель должен захотеть дочитать после первой же строки.
- Дальше — короткие абзацы по 1-3 предложения. Между абзацами всегда пустая строка.
- Обязательно один конкретный пример или мини-история в середине текста — про обычного человека,
  историческую личность или ситуацию из жизни. Без общих рассуждений, только конкретика.
- Разговорный тон, будто пишешь другу: обращение на "вы", живые формулировки, можно короткие
  риторические вопросы к читателю.
- Если нужен список — оформляй через дефис в начале строки (- пункт), никаких формальных
  подзаголовков-заголовков секций, никакой академической структуры "введение/основная часть/вывод".
  Текст должен течь как единая история, а не как статья с разделами.
- В конце — не сухой вывод, а короткая мысль-вывод (1-2 предложения) и лёгкое вовлечение читателя:
  вопрос, приглашение поделиться своим опытом, или фраза, подталкивающая задуматься.
- Один-два уместных эмодзи по тексту для визуальных акцентов — не больше, без спама эмодзи.

ЧЕГО ИЗБЕГАТЬ:
- Никакого канцелярита и штампов: "актуальность темы", "в данной статье", "подводя итог",
  "это важно", "многие сталкиваются с этой проблемой".
- Никаких markdown-символов: без **, ##, |, таблиц. Только обычный текст и дефисы для списков.
- Не повторяй одну мысль разными словами в разных абзацах.

ДЛИНА: строго 2800–3800 знаков с пробелами (примерно 400–540 слов). Это важно: пост должен
целиком помещаться в ОДНО сообщение Telegram (лимит 4096 знаков), поэтому не превышай верхнюю
границу — лучше чуть короче и ёмче, чем длиннее.

Прежде чем писать, продумай: какая одна мысль должна зацепить читателя и остаться в голове
после прочтения? Весь пост должен вести именно к ней.

Ответь только готовым текстом поста, без комментариев до или после."""

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


def markdown_line_to_html(line):
    """Конвертирует markdown-разметку внутри строки в Telegram HTML: **bold** -> <b>bold</b>."""
    line = escape_html(line)
    # **bold** или __bold__
    line = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", line)
    line = re.sub(r"__(.+?)__", r"<b>\1</b>", line)
    # одиночные *italic* (после обработки жирного, чтобы не спутать с **)
    line = re.sub(r"(?<!\*)\*([^*\n]+?)\*(?!\*)", r"<i>\1</i>", line)
    return line


def is_fully_bold(line):
    """Проверяет, обёрнута ли вся строка целиком в **bold** или __bold__ (модель уже сделала её жирной)."""
    stripped = line.strip()
    return bool(re.fullmatch(r"\*\*(.+)\*\*", stripped) or re.fullmatch(r"__(.+)__", stripped))


def convert_markdown_table(table_lines):
    """
    Превращает markdown-таблицу (строки вида | a | b | c |) в читаемый список
    для Telegram, где таблицы не рендерятся.
    """
    rows = []
    for raw in table_lines:
        cells = [c.strip() for c in raw.strip().strip("|").split("|")]
        # строка-разделитель вида |---|---| пропускаем
        if all(re.fullmatch(r":?-+:?", c) for c in cells if c):
            continue
        rows.append(cells)

    if not rows:
        return ""

    header, *data_rows = rows
    output = []
    for row in data_rows:
        parts = []
        for h, v in zip(header, row):
            h_clean = markdown_line_to_html(h)
            v_clean = markdown_line_to_html(v)
            parts.append(f"<b>{h_clean}:</b> {v_clean}")
        output.append(" — ".join(parts))
    return "\n".join(output)


def format_article_html(article):
    """
    Превращает текст статьи (возможно, с markdown-разметкой от модели) в HTML для Telegram:
    - заголовки (# ## ###) и короткие строки-подзаголовки — жирным
    - **bold** / __bold__ / *italic* — конвертируются в HTML-теги
    - markdown-таблицы — конвертируются в читаемый список (Telegram таблицы не рендерит)
    - маркеры списков "- "/"* " в начале строки — заменяются на "• "
    """
    raw_lines = [l.rstrip() for l in article.split("\n")]
    while raw_lines and not raw_lines[0].strip():
        raw_lines.pop(0)
    while raw_lines and not raw_lines[-1].strip():
        raw_lines.pop()

    if not raw_lines:
        return article

    html_parts = []
    i = 0
    title_done = False

    while i < len(raw_lines):
        line = raw_lines[i].strip()

        if not line:
            i += 1
            continue

        # markdown-таблица: собираем все подряд идущие строки с "|"
        if line.startswith("|") and line.endswith("|"):
            table_block = []
            while i < len(raw_lines) and raw_lines[i].strip().startswith("|"):
                table_block.append(raw_lines[i].strip())
                i += 1
            html_parts.append(convert_markdown_table(table_block))
            continue

        # markdown-заголовок: #, ##, ### в начале строки
        heading_match = re.match(r"^#{1,6}\s+(.*)", line)
        if heading_match:
            content = heading_match.group(1)
            html_parts.append(f"<b>{markdown_line_to_html(content)}</b>")
            i += 1
            title_done = True
            continue

        # маркер списка "- " или "* " в начале строки -> "• "
        bullet_match = re.match(r"^[\-\*⁃–—•]\s+(.*)", line)
        if bullet_match:
            html_parts.append(f"• {markdown_line_to_html(bullet_match.group(1))}")
            i += 1
            continue

        converted = markdown_line_to_html(line)
        already_bold = is_fully_bold(line)

        if not title_done:
            # первая содержательная строка статьи — заголовок, всегда жирным
            html_parts.append(converted if already_bold else f"<b>{converted}</b>")
            title_done = True
            i += 1
            continue

        # эвристика "похоже на подзаголовок" — короткая строка без точки в конце
        word_count = len(line.split())
        looks_like_subheading = (
            word_count <= 6
            and not line.endswith((".", "!", "?", ",", ":"))
            and len(line) < 60
        )
        if already_bold:
            html_parts.append(converted)
        elif looks_like_subheading:
            html_parts.append(f"<b>{converted}</b>")
        else:
            html_parts.append(converted)
        i += 1

    return "\n\n".join(html_parts)


def escape_html(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def send_to_telegram(topic, article):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]  # username канала (@my_channel) или его числовой id (-100...)
    url = f"https://api.telegram.org/bot{token}/sendMessage"

    formatted = format_article_html(article)
    safe_limit = TELEGRAM_MESSAGE_LIMIT - 50  # небольшой запас на всякий случай

    # Подстраховка: если из-за HTML-разметки (<b> и т.д.) текст всё же не влез —
    # просим модель сократить именно исходный текст и форматируем заново, а не режем на части.
    attempts = 0
    while len(formatted) > safe_limit and attempts < 2:
        print(f"После форматирования пост {len(formatted)} знаков — длиннее лимита, сокращаю...", flush=True)
        shrink_prompt = f"""Вот пост:

{article}

Он получился слишком длинным для одного сообщения Telegram. Сократи его до 2500–3000 знаков
с пробелами, сохранив стиль, крючок в начале и главную мысль. Убери менее важные детали,
а не просто обрывай текст. Ответь только сокращённым текстом поста."""
        article = ask_groq(shrink_prompt, max_tokens=4000)
        formatted = format_article_html(article)
        attempts += 1

    if len(formatted) > safe_limit:
        raise RuntimeError(
            f"Пост всё ещё превышает лимит Telegram после сокращения ({len(formatted)} знаков). "
            "Запустите бота ещё раз."
        )

    resp = requests.post(url, data={
        "chat_id": chat_id,
        "text": formatted,
        "parse_mode": "HTML",
    })
    if not resp.ok:
        print(f"Ошибка Telegram: {resp.status_code} {resp.text}")
    resp.raise_for_status()


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

"""
Бот для Яндекс Дзена: сам придумывает тему, пишет статью, генерирует картинку
и публикует всё прямо в Telegram-канал (без ручного копирования).

Полностью бесплатная схема:
  - генерация текста — Groq API (бесплатный тариф, щедрый лимит — до 14 400 запросов/день)
  - генерация картинки — Pollinations.ai (бесплатно, без API-ключа)
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

TELEGRAM_MESSAGE_LIMIT = 4096   # лимит Telegram на обычное текстовое сообщение (без фото)
TELEGRAM_CAPTION_LIMIT = 1024   # лимит Telegram на подпись к фото — используем именно его,
                                 # так как пост теперь всегда идёт как подпись под картинкой

# Целевая длина самого текста поста (без HTML-тегов). Взята с запасом от лимита подписи (1024),
# чтобы после добавления <b>...</b> для заголовка пост всё равно помещался в одну подпись к фото.
TARGET_MIN_CHARS = 650
TARGET_MAX_CHARS = 850

# Общая ниша канала — задаёт тон и рамку для тем.
# Отредактируйте под свой канал (сейчас настроено под "Апгрейд" — саморазвитие).
CHANNEL_THEME = "саморазвитие, психология привычек, продуктивность, ментальное здоровье"

# Файл, где бот запоминает уже использованные темы, чтобы не повторяться
USED_TOPICS_FILE = "used_topics.json"

# Бесплатная модель Groq с щедрым дневным лимитом и хорошим качеством текста
GROQ_MODEL = "openai/gpt-oss-120b"

client = Groq(api_key=os.environ["GROQ_API_KEY"])


class EmptyResponseError(Exception):
    """Модель вернула пустой ответ — обычно из-за того, что reasoning-модель потратила
    весь бюджет токенов на внутренние 'размышления' и не успела написать сам ответ."""
    pass


def ask_groq(prompt, max_tokens=3000, retries=5):
    for attempt in range(retries):
        try:
            resp = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_completion_tokens=max_tokens,
                reasoning_effort="low",  # меньше "размышлений" — больше шансов уложиться в токены
                timeout=120,
            )
            content = resp.choices[0].message.content
            if not content or not content.strip():
                raise EmptyResponseError("Модель вернула пустой ответ")
            return content.strip()
        except EmptyResponseError as e:
            wait = 15 * (attempt + 1)
            print(f"{e}. Увеличиваю бюджет токенов и пробую снова через {wait} сек...", flush=True)
            max_tokens = int(max_tokens * 1.5)  # даём больше места на "размышления" + ответ
            time.sleep(wait)
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
        try:
            with open(USED_TOPICS_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if not content:
                    return []
                return json.loads(content)
        except json.JSONDecodeError as e:
            print(f"Файл {USED_TOPICS_FILE} повреждён ({e}) — начинаю с пустого списка тем.", flush=True)
            return []
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
Напиши короткий пост-подпись к фото на тему: «{topic}»

Это короткий формат — как подпись под фото в Instagram или Telegram, а не полноценная статья.
Ёмкость важнее объёма: одна яркая мысль, поданная метко, а не попытка раскрыть тему полностью.

КАК ПИШУТ ТАКИЕ КАНАЛЫ (обязательно копируй этот стиль):
- Первая строка — цепляющий крючок: провокационное утверждение, вопрос в лоб или неожиданный
  факт. Читатель должен захотеть дочитать после первой же строки.
- Короткие абзацы по 1-2 предложения. Между абзацами пустая строка.
- Один короткий конкретный пример или образ — без развёрнутой истории, буквально одна яркая деталь
  или сравнение, которое иллюстрирует мысль.
- Разговорный тон, будто пишешь другу: обращение на "вы", живые формулировки.
- Никаких списков, подзаголовков, разделов — только цельный короткий текст, льющийся как одна мысль.
- Концовка — короткая фраза-вывод или вопрос к читателю для вовлечения (1 строка).
- Максимум один уместный эмодзи в конце, не больше.

ЧЕГО ИЗБЕГАТЬ:
- Никакого канцелярита: "актуальность темы", "в данной статье", "подводя итог", "это важно".
- Никаких markdown-символов: **, ##, |, таблиц, разделителей (---, ***), чекбоксов [ ], списков.
- Не пытайся раскрыть тему со всех сторон — только одна мысль, метко поданная.

ДЛИНА: строго 650–850 знаков с пробелами (примерно 90-120 слов). Это критично важно: пост
идёт подписью к фото в Telegram, а лимит подписи — 1024 знака. Лучше короче и ёмче, чем длиннее.

Ответь только готовым текстом поста, без комментариев до или после."""

    article = ask_groq(prompt, max_tokens=2000)
    return article


def regenerate_if_wrong_length(topic, article, attempts=2):
    """Если длина не попала в диапазон — просим переписать короче/длиннее."""
    for _ in range(attempts):
        length = len(article)
        if TARGET_MIN_CHARS <= length <= TARGET_MAX_CHARS:
            return article

        direction = "короче" if length > TARGET_MAX_CHARS else "длиннее"
        fix_prompt = f"""Вот пост на тему «{topic}»:

{article}

Текущая длина: {length} знаков. Нужно {direction}, чтобы уложиться СТРОГО в {TARGET_MIN_CHARS}–{TARGET_MAX_CHARS}
знаков с пробелами. Перепиши пост целиком с учётом этого, сохранив стиль и главную мысль."""

        article = ask_groq(fix_prompt, max_tokens=2000)

    return article


# ---------- Шаг 3. Опубликовать в Telegram-канал ----------


def markdown_line_to_html(line):
    """Конвертирует markdown-разметку внутри строки в Telegram HTML: **bold** -> <b>bold</b>."""
    line = escape_html(line)
    # [текст ссылки](url) -> просто текст ссылки, без адреса
    line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)
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

        # горизонтальный разделитель markdown (---, ***, ___) — просто убираем, без замены
        if re.fullmatch(r"[\-\*_]{3,}", line.replace(" ", "")):
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

        # маркер списка "- "/"* "/"⁃ " в начале строки -> универсальный "- "
        bullet_match = re.match(r"^[\-\*⁃–—•]\s+(.*)", line)
        if bullet_match:
            content = bullet_match.group(1)
            # убираем чекбокс-разметку "[ ] " или "[x] ", если модель её добавила
            content = re.sub(r"^\[[ xX]?\]\s*", "", content)
            html_parts.append(f"- {markdown_line_to_html(content)}")
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


def safe_truncate_html(html, limit):
    """
    Обрезает HTML-текст до limit знаков по границе абзаца (\n\n), не разрывая слова,
    и закрывает все незакрытые теги <b>/<i>, чтобы Telegram не отклонил сообщение
    из-за битой разметки.
    """
    if len(html) <= limit:
        return html

    paragraphs = html.split("\n\n")
    kept = []
    total = 0
    for p in paragraphs:
        addition = len(p) + (2 if kept else 0)
        if total + addition > limit:
            break
        kept.append(p)
        total += addition

    truncated = "\n\n".join(kept) if kept else html[:limit]

    # закрываем незакрытые теги, если абзац оборвался посреди форматирования
    for tag in ("b", "i"):
        opens = truncated.count(f"<{tag}>")
        closes = truncated.count(f"</{tag}>")
        if opens > closes:
            truncated += f"</{tag}>" * (opens - closes)

    return truncated


def prepare_formatted_post(article, char_limit):
    """
    Форматирует статью под HTML и гарантирует, что итоговый текст уложится в char_limit знаков —
    сначала пробует попросить модель сократить, а если не получится, обрезает сама по границе
    абзаца. Возвращает (formatted_html, article_text_used).
    """
    formatted = format_article_html(article)
    safe_limit = char_limit - 30  # небольшой запас на всякий случай

    shrink_targets = [
        (int(char_limit * 0.55), int(char_limit * 0.65)),
        (int(char_limit * 0.40), int(char_limit * 0.50)),
        (int(char_limit * 0.25), int(char_limit * 0.35)),
    ]
    attempt = 0
    while len(formatted) > safe_limit and attempt < len(shrink_targets):
        lo, hi = shrink_targets[attempt]
        print(f"После форматирования пост {len(formatted)} знаков — длиннее лимита ({char_limit}), сокращаю до {lo}-{hi}...", flush=True)
        shrink_prompt = f"""Вот пост:

{article}

Он получился слишком длинным. Сократи его СТРОГО до {lo}–{hi} знаков с пробелами (жёсткое
требование, не превышай {hi}). Сохрани стиль, крючок в начале и главную мысль, убери менее
важные детали, а не обрывай текст на середине. Ответь только сокращённым текстом поста."""
        previous_article = article
        try:
            article = ask_groq(shrink_prompt, max_tokens=1500)
        except Exception as e:
            print(f"Не удалось сократить пост ({e}) — оставляю предыдущую версию.", flush=True)
            article = previous_article
            break
        new_formatted = format_article_html(article)
        if not new_formatted.strip():
            print("Сокращённая версия оказалась пустой — оставляю предыдущую.", flush=True)
            article = previous_article
            break
        formatted = new_formatted
        attempt += 1

    if len(formatted) > safe_limit:
        print(f"Модель не уложилась в лимит ({len(formatted)} знаков) — обрезаю вручную.", flush=True)
        formatted = safe_truncate_html(formatted, safe_limit)

    return formatted, article


def send_photo_with_caption(image_bytes, article):
    """Отправляет фото с текстом поста как подписью — одно сообщение в канале.
    Возвращает True при успехе."""
    formatted, _ = prepare_formatted_post(article, TELEGRAM_CAPTION_LIMIT)
    send_photo_to_telegram(image_bytes, formatted)
    return True


def send_to_telegram(topic, article):
    """Запасной путь: отправка обычным текстовым сообщением, если картинку не удалось получить."""
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]  # username канала (@my_channel) или его числовой id (-100...)
    url = f"https://api.telegram.org/bot{token}/sendMessage"

    formatted, _ = prepare_formatted_post(article, TELEGRAM_MESSAGE_LIMIT)

    resp = requests.post(url, data={
        "chat_id": chat_id,
        "text": formatted,
        "parse_mode": "HTML",
    })
    if not resp.ok:
        print(f"Ошибка Telegram: {resp.status_code} {resp.text}")
    resp.raise_for_status()



# ---------- Шаг 4. Сгенерировать и отправить картинку к посту ----------

def generate_image_prompt(topic, article):
    """Просит модель придумать короткий промпт на английском для генерации картинки —
    опираясь на конкретный образ/пример из УЖЕ НАПИСАННОГО текста поста, а не на абстрактную
    тему, чтобы картинка реально соответствовала содержанию, а не была случайной."""
    prompt = f"""Вот пост, который был опубликован в Telegram-канале о саморазвитии:

{article}

Придумай короткий промпт на английском языке для генерации иллюстрации к ЭТОМУ конкретному
посту.

Требования:
- Найди в тексте поста конкретный образ, пример, сравнение или сцену (не абстрактную идею
  вроде "саморазвитие" или "мотивация") и опиши именно её визуально.
- Стиль: минималистичная плоская иллюстрация (flat illustration), мягкие тёплые цвета,
  спокойная эстетика, без текста и надписей на картинке, без человеческих лиц крупным планом.
- Промпт должен описывать одну конкретную визуальную сцену, которую можно нарисовать —
  предметы, обстановку, действие, а не общие понятия.
- Длина: одна строка, 12-20 слов на английском.

Ответь только текстом промпта, без пояснений и кавычек."""

    try:
        return ask_groq(prompt, max_tokens=500, retries=2)
    except Exception as e:
        print(f"Не удалось придумать промпт для картинки ({e}) — использую тему напрямую.", flush=True)
        return f"minimalist flat illustration, warm colors, concept of {topic}, no text"


def fetch_image_bytes(image_prompt, retries=3):
    """Получает картинку с бесплатного сервиса Pollinations.ai (без API-ключа)."""
    import urllib.parse
    encoded = urllib.parse.quote(image_prompt)
    seed = int(time.time())
    url = (
        f"https://image.pollinations.ai/prompt/{encoded}"
        f"?width=1024&height=1024&nologo=true&seed={seed}&model=flux&enhance=true"
    )

    for attempt in range(retries):
        try:
            resp = requests.get(url, timeout=60)
            resp.raise_for_status()
            if resp.headers.get("content-type", "").startswith("image/"):
                return resp.content
            raise ValueError(f"Сервис вернул не картинку: {resp.headers.get('content-type')}")
        except Exception as e:
            wait = 10 * (attempt + 1)
            print(f"Не удалось получить картинку ({e}). Пробую снова через {wait} сек...", flush=True)
            time.sleep(wait)

    return None


def send_photo_to_telegram(image_bytes, caption_html):
    """Отправляет картинку с подписью — фото и текст уходят ОДНИМ сообщением в канал."""
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    url = f"https://api.telegram.org/bot{token}/sendPhoto"

    resp = requests.post(
        url,
        data={
            "chat_id": chat_id,
            "caption": caption_html,
            "parse_mode": "HTML",
        },
        files={"photo": ("image.jpg", image_bytes, "image/jpeg")},
        timeout=60,
    )
    if not resp.ok:
        print(f"Ошибка отправки картинки: {resp.status_code} {resp.text}", flush=True)
    resp.raise_for_status()


# ---------- Основной сценарий ----------

def main():
    print("Придумываю тему...", flush=True)
    topic = generate_topic()
    print(f"Тема: {topic}", flush=True)

    print("Пишу пост...", flush=True)
    article = generate_article(topic)
    article = regenerate_if_wrong_length(topic, article)

    print(f"Готово. Длина: {len(article)} знаков.", flush=True)

    print("Придумываю картинку...", flush=True)
    image_prompt = generate_image_prompt(topic, article)
    print(f"Промпт картинки: {image_prompt}", flush=True)
    image_bytes = fetch_image_bytes(image_prompt)

    published = False
    if image_bytes:
        print("Отправляю фото с подписью в Telegram (одним сообщением)...", flush=True)
        try:
            send_photo_with_caption(image_bytes, article)
            published = True
        except Exception as e:
            print(f"Не удалось отправить фото с подписью ({e}) — публикую только текст.", flush=True)
    else:
        print("Картинка не получена — публикую только текст.", flush=True)

    if not published:
        print("Отправляю текст в Telegram...", flush=True)
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

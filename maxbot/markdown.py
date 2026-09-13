"""Конвертация LLM-markdown в безопасный markdown MAX."""
import re

_HTML_RE = re.compile(r"</?[a-zA-Z][^>\n]*>")
_HEAD_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$", re.MULTILINE)
_HR_RE = re.compile(r"^[ \t]{0,3}(?:-{3,}|\*{3,}|_{3,})[ \t]*$", re.MULTILINE)
_HR_GLYPH = "─────────────"  # MAX не поддерживает ---; рендерим линией

# списки не поддерживаются: чекбоксы и буллеты конвертируем (вне код-блоков)
_QUOTE_RE = re.compile(r"^(\s*)(?:>\s?)+", re.MULTILINE)  # MAX: только один уровень цитат

_SESSION_RE = re.compile(r"@session:[A-Za-z0-9_/\-]+")

_TASK_RE = re.compile(r"^(\s*)[-*+] +\[( |x|X)\] +", re.MULTILINE)
_BULLET_RE = re.compile(r"^(\s*)[-*+] +", re.MULTILINE)


def _outside_code(text: str, fn) -> str:
    """Применить fn к частям текста вне fenced-кодов (``` ... ```)."""
    parts = re.split(r"(```.*?```)", text, flags=re.DOTALL)
    return "".join(p if p.startswith("```") else fn(p) for p in parts)

_LINK_RE = re.compile(r"\[([^\]]*)\]\((https?://[^)\s]+)\)")

_MAX_URL = 2048


def _strip_html(text: str) -> str:
    return _HTML_RE.sub("", text)


def _headings_to_bold(text: str) -> str:
    return _HEAD_RE.sub(lambda m: f"**{m.group(2)}**", text)


_ZERO_WIDTH = ("\ufe0e", "\ufe0f", "\u200d", "\u200b", "\ufeff")  # VS15/16, ZWJ и пр.


def _cell_width(cell: str) -> int:
    """Ширина ячейки в моноширинном рендере: эмодзи и широкие символы занимают 2 колонки.
    Variation-selector'ы и joiner'ы имеют нулевую ширину (иначе 🅰️ считается за 4)."""
    import unicodedata
    width = 0
    for ch in cell:
        if ch in _ZERO_WIDTH or unicodedata.combining(ch):
            continue
        width += 2 if unicodedata.east_asian_width(ch) in "WF" or ord(ch) > 0x2100 else 1
    return width


def _pad(cell: str, width: int) -> str:
    return cell + " " * max(0, width - _cell_width(cell))


_SEP_CHARS = set("-:\u2014\u2013\u2012 ")


def _split_cells(line: str):
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    return cells if len(cells) > 1 else None


def _is_table_line(line: str) -> bool:
    return "|" in line and line.strip() != ""


def _is_sep_line(line: str) -> bool:
    cells = _split_cells(line)
    return bool(cells) and all(c and set(c) <= _SEP_CHARS for c in cells)


def _strip_inline_md(cell: str) -> str:
    """В моноширинном блоке маркеры бесполезны — **, __, `, * из ячеек убираем."""
    import re as _re
    return _re.sub(r"\*\*|__|`|\*", "", cell).strip()


def _render_table(block) -> str:
    rows, aligns = [], []
    for ln in block:
        stripped = ln.rstrip("\n")
        cells = _split_cells(stripped)
        if cells is not None:
            cells = [_strip_inline_md(c) for c in cells]
        if cells is None:
            continue
        if _is_sep_line(stripped):
            if not aligns:
                aligns = ["center" if c.startswith(":") and c.endswith(":")
                          else "right" if c.endswith(":") else "left" for c in cells]
            continue
        rows.append(cells)
    if not rows:
        return "".join(block)
    ncols = max(len(r) for r in rows)
    for r in rows:
        r.extend([""] * (ncols - len(r)))
    if len(aligns) < ncols:
        aligns = aligns + ["left"] * (ncols - len(aligns))
    widths = [max(_cell_width(r[i]) for r in rows) for i in range(ncols)]

    def _cell(row, i):
        cell, w, a = row[i], widths[i], aligns[i]
        gap = max(0, w - _cell_width(cell))
        if a == "right":
            return " " * gap + cell
        if a == "center":
            left = gap // 2
            return " " * left + cell + " " * (gap - left)
        return cell + " " * gap

    total_w = sum(widths) + 2 * (ncols - 1)
    if ncols >= 2 and total_w > 60:
        return _render_vertical(rows)  # широкие таблицы — вертикальные карточки
    out = ["  ".join(_cell(r, i) for i in range(ncols)).rstrip() for r in rows]
    return "```\n" + "\n".join(out) + "\n```"


def _render_vertical(rows) -> str:
    """Широкая таблица → карточки: мобильный рендер, без горизонтального скролла."""
    headers = rows[0]
    chunks = []
    for r in rows[1:] if len(rows) > 1 else rows:
        head = (r[0] or "").strip() or "—"
        lines = [f"▪ *{head}*" if head != "—" else "▪"]
        for i in range(1, len(r)):
            if not (r[i] or "").strip():
                continue
            label = (headers[i] or "").strip()
            lines.append(f"   {('*' + label + ':* ') if label else ''}{r[i]}")
        chunks.append("\n".join(lines))
    return "\n".join(chunks)


def _tables_to_code(text: str) -> str:
    """Построчный парсер таблиц: сепаратор-строка + соседние строки с '|'.
    Внешние пайпы и заголовок опциональны; тире-разделители нормализуются."""
    lines = text.splitlines(keepends=True)
    out, i = [], 0
    while i < len(lines):
        cur = lines[i].rstrip("\n")
        sep_here = _is_table_line(cur) and _is_sep_line(cur)
        header_like = _is_table_line(cur) and not sep_here
        nxt = lines[i + 1].rstrip("\n") if i + 1 < len(lines) else ""
        starts = (header_like and _is_sep_line(nxt)) or (sep_here and _is_table_line(nxt))
        if not starts:
            out.append(lines[i])
            i += 1
            continue
        j = i + 2 if header_like else i + 1
        while j < len(lines) and _is_table_line(lines[j]) and not _is_sep_line(lines[j]):
            j += 1
        rendered = _render_table(lines[i:j])
        if out and not out[-1].endswith("\n"):
            out.append("\n")  # fence/карточки начинаются с новой строки
        out.append(rendered if rendered.endswith("\n") else rendered + "\n")
        i = j
    return "".join(out)


def _clamp_links(text: str) -> str:
    def _repl(m: re.Match) -> str:
        label, url = m.group(1), m.group(2)
        if len(url) <= _MAX_URL:
            return m.group(0)
        return f"{label}: {url[:_MAX_URL - 3]}..."
    return _LINK_RE.sub(_repl, text)


_PROTECT_RE = re.compile(r"```.*?```|`[^`\n]*`|\[[^\]]*\]\([^)]*\)", re.DOTALL)
_WORD_RE = re.compile(r"\w", re.UNICODE)


def _escape_stray(text: str) -> str:
    """Экранируем одиночные * и _ вне код-блоков, инлайн-кода и markdown-ссылок.

    Внутри незащищённого сегмента маркеры сопоставляются попарно
    (открывающий — за ним не-пробел; закрывающий — перед ним не-пробел);
    непарные одиночные маркеры экранируются. Пробеги из 2+ одинаковых
    символов (** и __) не трогаются.
    """
    parts = []
    last = 0
    for m in _PROTECT_RE.finditer(text):
        parts.append(_escape_segment(text[last:m.start()]))
        parts.append(m.group(0))
        last = m.end()
    parts.append(_escape_segment(text[last:]))
    return "".join(parts)


def _escape_segment(segment: str) -> str:
    for ch in ("*", "_"):
        segment = _escape_marker(segment, ch)
    return segment


def _escape_marker(segment: str, ch: str) -> str:
    n = len(segment)
    singles = []
    for i, c in enumerate(segment):
        if c != ch:
            continue
        if i > 0 and segment[i - 1] in ("\\", ch):
            continue
        if i < n - 1 and segment[i + 1] == ch:
            continue
        singles.append(i)
    if not singles:
        return segment
    paired = set()
    pending = -1
    for i in singles:
        before = segment[i - 1] if i > 0 else ""
        after = segment[i + 1] if i < n - 1 else ""
        # intraword-подчёркивания (foo_bar) оставляем как есть
        if ch == "_" and _WORD_RE.fullmatch(before) and _WORD_RE.fullmatch(after):
            continue
        can_close = bool(before) and not before.isspace()
        can_open = bool(after) and not after.isspace()
        if pending >= 0 and can_close:
            paired.add(pending)
            paired.add(i)
            pending = -1
        elif can_open:
            pending = i
    if len(paired) == len(singles):
        return segment
    out = []
    prev = 0
    for i in singles:
        if i in paired:
            continue
        out.append(segment[prev:i])
        out.append("\\" + ch)
        prev = i + 1
    out.append(segment[prev:])
    return "".join(out)


def comment_markdown(text: str) -> str:
    """Markdown для комментариев каналов: MAX не поддерживает ссылки/упоминания —
    разворачиваем [текст](url) в «текст: url»."""
    return _LINK_RE.sub(lambda m: f"{m.group(1)}: {m.group(2)}", sanitize_markdown(text))


def sanitize_markdown(text: str) -> str:
    # всё, что меняет структуру, — ТОЛЬКО вне код-блоков (вложенные ``` ломают разметку)
    text = _outside_code(text, _strip_html)
    text = _outside_code(text, lambda t: _HR_RE.sub(_HR_GLYPH, t))

    def _lists(chunk: str) -> str:
        chunk = _QUOTE_RE.sub(lambda m: m.group(1) + "> ", chunk)
        chunk = _TASK_RE.sub(
            lambda m: m.group(1) + ("☑ " if m.group(2).lower() == "x" else "☐ "), chunk)
        return _BULLET_RE.sub(lambda m: m.group(1) + "• ", chunk)

    text = _outside_code(text, _lists)
    # ядро оборачивает подсказки в "_..._"; MAX не рендерит italic поверх строки с кодом — распаковываем
    text = re.sub(r"(?m)^_(.+)_\s*$", lambda mm: mm.group(1) if "`" in mm.group(1) else mm.group(0), text)
    text = _SESSION_RE.sub("", text)
    text = re.sub(r"[ \t]+(?:в|на|из|in)[ \t]+\(", " (", text)  # «Подробности в (» → «Подробности (»
    text = re.sub(r"[ \t]+([.,;:!?])", r"\1", text)
    text = re.sub(r"(?m)[ \t]{2,}", " ", text)
    text = _outside_code(text, _tables_to_code)
    text = _outside_code(text, _headings_to_bold)
    text = _clamp_links(text)
    text = _escape_stray(text)
    return text.strip()

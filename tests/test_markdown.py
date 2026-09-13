from maxbot.markdown import sanitize_markdown


def test_headings_to_bold():
    assert sanitize_markdown("# Заголовок\n## Ещё") == "**Заголовок**\n**Ещё**"


def test_html_stripped():
    assert sanitize_markdown("привет <b>мир</b> <a href='x'>ссылка</a>") == "привет мир ссылка"


def test_table_to_code_block():
    md = "| Тип | Примечание |\n|---|---|\n| Картинки | ок |"
    out = sanitize_markdown(md)
    assert out.startswith("```") and out.endswith("```")
    assert "---" not in out and "|" not in out  # пайпы и разделитель убраны
    lines = out.strip("`\n").splitlines()
    assert lines[0] == "Тип       Примечание"  # колонки выровнены по ширине
    assert lines[1] == "Картинки  ок"


def test_long_link_clamped():
    url = "https://x.ru/" + "a" * 2100
    out = sanitize_markdown(f"[текст]({url})")
    assert "https://x.ru/" in out and len(out) < len(url)


def test_stray_odd_markers_escaped():
    out = sanitize_markdown("цена 5* 3 и _ хвост")
    assert "\\*" in out or "* 3" not in out  # одиночный * экранирован


def test_normal_markdown_untouched():
    text = "**жирный** *курсив* `code` [ссылка](https://ok.ru) > цитата"
    assert sanitize_markdown(text) == text


def test_escape_stray_ignores_urls_and_code():
    text = "[док](https://x.ru/a_b_c) и `sn_a_ke` код"
    out = sanitize_markdown(text)
    assert "(https://x.ru/a_b_c)" in out
    assert "`sn_a_ke`" in out
    assert "\\_" not in out  # внутри URL и кода ничего не экранировано


def test_two_independent_stray_stars_escaped():
    out = sanitize_markdown("5* 3 и 7* 2")
    assert "\\*" in out and out.count("\\*") == 2  # каждый одиночный экранирован


def test_paired_markers_survive():
    text = "**жирный** и _курсив_ и `code` и [ссылка](https://ok.ru/a_b)"
    assert sanitize_markdown(text) == text


def test_horizontal_rules_converted():
    out = sanitize_markdown("до\n---\nпосле\n\n***\n\n___")
    assert "─────────────" in out
    assert not any(ln.strip() in {"---", "***", "___"} for ln in out.splitlines())


def test_checklists_and_bullets():
    out = sanitize_markdown("- [ ] купить хлеб\n- [x] done\n- обычный пункт")
    assert "☐ купить хлеб" in out and "☑ done" in out and "• обычный пункт" in out


def test_lists_untouched_inside_code():
    src = "```\n- [ ] в коде\n- буллет в коде\n```"
    assert sanitize_markdown(src) == src


def test_nested_quotes_flattened():
    out = sanitize_markdown("> > вложенная\n>> тоже")
    assert "> вложенная" in out and "> тоже" in out
    assert "> >" not in out and ">>" not in out


def test_plain_quote_untouched():
    assert sanitize_markdown("> Цитата") == "> Цитата"


def test_table_alignment_respected():
    md = "| Влево | По центру | Вправо |\n|:---|:---:|---:|\n| 1 | 2 | 3 |\n| текст | текст | текст |"
    out = sanitize_markdown(md)
    lines = out.strip("`\n").splitlines()
    assert lines[1].startswith(" ") is False  # левая — без отступа
    assert lines[1].split("  ")[0] == "1"
    # правая колонка: «3» и «текст» прижаты вправо одинаково
    assert lines[1].rstrip().endswith("3") and lines[2].rstrip().endswith("текст")


def test_table_without_header_row():
    md = "|:---:|:---:|:---:|\n| раз | два | три |\n| X | Y | Z |"
    out = sanitize_markdown(md)
    assert out.startswith("```") and "|" not in out
    lines = out.strip("`\n").splitlines()
    assert lines[0].strip().startswith("раз")


def test_emoji_variation_selector_width():
    from maxbot.markdown import _cell_width
    assert _cell_width("\U0001f170\ufe0f") == 2  # 🅰️ = глиф + VS16
    assert _cell_width("a") == 1
    # таблица с эмодзи-ключкапами выравнивается как обычный текст
    md = "| \U0001f170\ufe0f | a |\n|:---:|:---:|\n| x | y |"
    out = sanitize_markdown(md)
    assert out.strip("`\n").splitlines()[0].startswith("\U0001f170\ufe0f")


def test_table_cells_inline_md_stripped():
    out = sanitize_markdown("| **Итого** | **≈753 400 ₽** |\n|---|---:|")
    assert "Итого" in out and "**" not in out


def test_tables_untouched_inside_code_block():
    src = "```\n| a | b |\n|---|---|\n| 1 | 2 |\n```"
    assert sanitize_markdown(src) == src


def test_hr_untouched_inside_code_block():
    src = "```\n---\n```"
    assert sanitize_markdown(src) == src


def test_wide_table_renders_vertical_cards():
    long_pos = "очень длинная позиция " * 3
    md = f"| Категория | Позиция | Цена |\n|---|---|---|\n| Кухня | {long_pos} | 552300 |"
    out = sanitize_markdown(md)
    assert "```" not in out  # без код-блока
    assert "▪ *Кухня*" in out and "*Позиция:*" in out and "552300" in out


def test_fence_never_glues_to_previous_line():
    md = "Список покупок:\n| a | b |\n|---|---|\n| 1 | 2 |"
    out = sanitize_markdown(md)
    assert "покупок:```" not in out
    assert "\n```" in out


def test_underscore_wrap_with_code_unwrapped():
    src = "_Text fallback: reply `/approve`, `/always`, or `/cancel`._"
    out = sanitize_markdown(src)
    assert out.startswith("Text fallback") and not out.startswith("_")

from maxbot.markdown import sanitize_markdown


def test_headings_to_bold():
    assert sanitize_markdown("# Заголовок\n## Ещё") == "**Заголовок**\n**Ещё**"


def test_html_stripped():
    assert sanitize_markdown("привет <b>мир</b> <a href='x'>ссылка</a>") == "привет мир ссылка"


def test_table_to_code_block():
    md = "| Тип | Примечание |\n|---|---|\n| Картинки | ок |"
    out = sanitize_markdown(md)
    assert "---" not in out  # разделитель убран
    assert "```" not in out  # fenced-блоки не отправляем: парсер MAX их ломает
    # каждая строка — инлайн-`код`: моноширинный рендер, колонки выровнены
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert lines[0] == "`Тип       Примечание`"  # 3 + 5 паддинга + 2 сепаратора
    assert lines[1] == "`Картинки  ок`"
    # выравнивание на обычных пробелах — они живучи внутри инлайн-кода
    assert "  " in out


def test_table_rows_monospace_idempotent():
    # повторный проход не должен съедать паддинг внутри инлайн-кода
    md = "| A | B |\n|---|---|\n| x | y |"
    once = sanitize_markdown(md)
    assert sanitize_markdown(once) == once


def test_table_alignment_survives_space_collapse():
    # NBSP-паддинг более не используется (карточки), но пробелы внутри
    # значений ячеек должны сохраняться как есть
    md = "| A | B |\n|---|---|\n| два слова | y |"
    out = sanitize_markdown(md)
    assert "два слова" in out


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
    # выравнивание снова работает: строки моноширинные (инлайн-код)
    md = "| Влево | По центру | Вправо |\n|:---|:---:|---:|\n| 1 | 2 | 3 |\n| текст | текст | текст |"
    out = sanitize_markdown(md)
    assert "```" not in out
    lines = [ln for ln in out.splitlines() if ln.strip()]
    # правая колонка прижата вправо: закрывающие бэктики всех строк
    # на одной позиции (последняя ячейка заканчивается у края)
    closes = [ln.index("`", 1) for ln in lines]
    assert closes[0] == closes[1] == closes[2]
    assert lines[1].endswith("3`") and lines[2].endswith("текст`")


def test_table_without_header_row():
    md = "|:---:|:---:|:---:|\n| раз | два | три |\n| X | Y | Z |"
    out = sanitize_markdown(md)
    assert "```" not in out
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert len(lines) == 2  # без заголовка — только строки данных
    assert lines[0].startswith("`раз")  # первая колонка выровнена по «раз»
    assert "X" in lines[1] and "Z" in lines[1]


def test_empty_cells_filled_with_dash():
    out = sanitize_markdown("| A | B |\n|---|---|\n| x |  |")
    assert "x  —" in out  # пустая ячейка — тире, колонка не схлопывается


def test_emoji_variation_selector_width():
    from maxbot.markdown import _cell_width
    assert _cell_width("\U0001f170\ufe0f") == 2  # 🅰️ = глиф + VS16
    assert _cell_width("a") == 1


def test_table_cells_inline_md_stripped():
    out = sanitize_markdown("| **Итого** | **≈753 400 ₽** |\n|---|---:|")
    # маркеры из ячеек зачищены, в моноширинной строке они не нужны
    assert "Итого" in out and "**" not in out and "`" in out


def test_tables_untouched_inside_code_block():
    src = "```\n| a | b |\n|---|---|\n| 1 | 2 |\n```"
    assert sanitize_markdown(src) == src


def test_hr_untouched_inside_code_block():
    src = "```\n---\n```"
    assert sanitize_markdown(src) == src


def test_wide_table_renders_vertical_cards():
    # шире 80 колонок — перенос на 3+ экрана тяжело читать: строка на запись с «|»
    long_pos = "очень длинная позиция " * 3
    md = f"| Категория | Позиция | Цена |\n|---|---|---|\n| Кухня | {long_pos} | 552300 |"
    out = sanitize_markdown(md)
    assert "```" not in out
    assert "`" not in out  # без моноширинных строк
    assert "Кухня |" in out and "552300" in out


def test_fence_never_glues_to_previous_line():
    md = "Список покупок:\n| a | b |\n|---|---|\n| 1 | 2 |"
    out = sanitize_markdown(md)
    assert "покупок:`" not in out
    assert "\n`" in out  # таблица начинается с новой строки


def test_underscore_wrap_with_code_unwrapped():
    src = "_Text fallback: reply `/approve`, `/always`, or `/cancel`._"
    out = sanitize_markdown(src)
    assert out.startswith("Text fallback") and not out.startswith("_")


# ── регрессия живого бага: таблица ломала рендер всего сообщения ──

def test_table_does_not_break_markdown_after_it():
    # MAX не поддерживает ``` — fenced-таблица «выключала» рендер хвоста:
    # сырые ** и бэктики. Таблица должна уйти плоским текстом, хвост — целым.
    md = ("| Сервис | Статус |\n|---|---|\n| example.org | 200 |\n\n"
          "Проверка доступности — **по гео-подсети**, см. `*.example.com`.")
    out = sanitize_markdown(md)
    assert "```" not in out
    assert "**по гео-подсети**" in out
    assert "`*.example.com`" in out


def test_intraword_underscores_untouched():
    # snake_case-идентификаторы не экранируются: MAX покажет \_ буквально
    assert sanitize_markdown("файл user_data_v2 обновлён") == "файл user_data_v2 обновлён"
    assert sanitize_markdown("kolmogorov_smirnov, p_value") == "kolmogorov_smirnov, p_value"


class TestDashedCommandAlias:
    """MAX-клиент обрезает команду по дефису при тапе (/x-y уходит как /x).
    Исходящий текст показывает _-форму, входящий адаптер переписывает обратно."""

    def test_outbound_dash_to_underscore(self):
        assert sanitize_markdown("/reload-mcp — перезагрузка") == \
            "`/reload_mcp` — перезагрузка"

    def test_outbound_list_prefix(self):
        assert sanitize_markdown("- /codex-runtime: выбор рантайма") == \
            "• `/codex_runtime`: выбор рантайма"

    def test_outbound_mid_text_untouched(self):
        text = "путь вида /usr/local-bin или /reload-mcp в тексте"
        assert sanitize_markdown(text) == text

    def test_outbound_url_untouched(self):
        text = "см. https://host/reload-mcp и max://user/9"
        assert sanitize_markdown(text) == text

    def test_outbound_plain_command_untouched(self):
        assert sanitize_markdown("/new /status") == "/new /status"

    def test_inbound_underscore_to_dash(self):
        from maxbot.adapter import _fix_dashed_command
        assert _fix_dashed_command("/reload_mcp") == "/reload-mcp"

    def test_inbound_with_args(self):
        from maxbot.adapter import _fix_dashed_command
        assert _fix_dashed_command("/claude_design сделай макет") == \
            "/claude-design сделай макет"

    def test_inbound_plain_text_untouched(self):
        from maxbot.adapter import _fix_dashed_command
        assert _fix_dashed_command("привет_мир") == "привет_мир"
        assert _fix_dashed_command("/status") == "/status"

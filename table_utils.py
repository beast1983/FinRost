"""Общие помощники для оформления таблиц (Treeview)."""

ZEBRA_ODD_BG = '#f5f5f5'
ZEBRA_EVEN_BG = '#ffffff'

# Теги-строки, которые не участвуют в зебре (заголовки групп и т.п.)
SKIP_TAGS = ('group_header',)


def apply_zebra(tree, odd_bg=ZEBRA_ODD_BG, even_bg=ZEBRA_EVEN_BG, skip_tags=SKIP_TAGS):
    """Применить чередование строк (зебра) к Treeview.

    Сохраняет функциональные теги строки (ID, раскраску прибыль/убыток и т.д.),
    добавляя тег чёт/нечет в конец. Строки с тегами из skip_tags пропускаются,
    не сбивая общую чётность.
    """
    tree.tag_configure('zebra_odd', background=odd_bg)
    tree.tag_configure('zebra_even', background=even_bg)

    parity = 0
    for item in tree.get_children():
        existing = tuple(
            t for t in tree.item(item, 'tags')
            if t not in ('zebra_odd', 'zebra_even')
        )

        if any(tag in skip_tags for tag in existing):
            tree.item(item, tags=existing)
            continue

        row_tag = 'zebra_odd' if parity % 2 else 'zebra_even'
        tree.item(item, tags=existing + (row_tag,))
        parity += 1


# Теги-строки, несущие собственный цвет фона (сравнение с прошлым месяцем и т.п.)
COLOR_TAGS = ('cmp_up', 'cmp_down')


def reveal_row_colors(tree, color_tags=COLOR_TAGS):
    """Снять зебру со строк, у которых есть собственный цветной тег.

    apply_zebra кладёт тег zebra_odd/zebra_even в конец списка тегов, а в Tk
    последний тег выигрывает при конфликте background. Чтобы проявился фон
    cmp_up/cmp_down, удаляем зебра-теги у цветных строк уже после apply_zebra
    (parity к этому моменту уже посчитан для всех строк).
    """
    for item in tree.get_children():
        tags = tuple(tree.item(item, 'tags'))
        if any(t in color_tags for t in tags):
            clean = tuple(t for t in tags if t not in ('zebra_odd', 'zebra_even'))
            tree.item(item, tags=clean)

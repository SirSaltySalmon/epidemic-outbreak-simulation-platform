"""WHO hub HTML discovery (DON item links)."""

from __future__ import annotations

from eosp.services.who_don_discover import (
    list_don_items_from_hub_html,
    sort_don_entries_newest_first,
)

HUB_TWO_ITEMS = """
<html><body>
<section>
  <p>4 May 2026 | Disease outbreak news</p>
  <a href="/emergencies/disease-outbreak-news/item/2026-DON-old">Older DON</a>
</section>
<section>
  <p>8 May 2026 | Disease outbreak news</p>
  <a href="https://www.who.int/emergencies/disease-outbreak-news/item/2026-DON-new">Newer DON</a>
</section>
</body></html>
"""


def test_list_don_items_dedupes_and_preserves_order() -> None:
    base = "https://www.who.int/emergencies/emergency-events/item/2026-e000227"
    items = list_don_items_from_hub_html(HUB_TWO_ITEMS, base)
    assert [e.don_item_id for e in items] == ["2026-DON-old", "2026-DON-new"]


def test_sort_newest_first_by_list_date() -> None:
    base = "https://www.who.int/emergencies/emergency-events/item/2026-e000227"
    items = list_don_items_from_hub_html(HUB_TWO_ITEMS, base)
    sorted_items = sort_don_entries_newest_first(items)
    assert sorted_items[0].don_item_id == "2026-DON-new"
    assert sorted_items[1].don_item_id == "2026-DON-old"


def test_path_only_href_resolves() -> None:
    html = '<a href="/emergencies/disease-outbreak-news/item/2026-DON-rel">x</a>'
    hub = "https://www.who.int/emergencies/emergency-events/item/2026-e000227"
    items = list_don_items_from_hub_html(html, hub)
    assert len(items) == 1
    assert items[0].don_item_id == "2026-DON-rel"
    assert items[0].url.endswith("/emergencies/disease-outbreak-news/item/2026-DON-rel")

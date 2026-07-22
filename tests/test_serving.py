"""
Tests for the pure logic behind diary writes.

These cover serving-size selection and the local entry-id store. Neither needs
network access or MyFitnessPal credentials, so they run anywhere.
"""

import json

import pytest

from mfp_mcp import server


@pytest.fixture
def food():
    """A food with several serving sizes, shaped like the v2 API returns them."""
    return {
        "id": "167417344692029",
        "version": "198470069641213",
        "description": "Grilled Chicken Breast",
        "serving_sizes": [
            {
                "id": "65976937717733",
                "value": 4.0,
                "unit": "oz",
                "nutrition_multiplier": 1.13,
                "gram_weight": 113.0,
                "index": 0,
            },
            {
                "id": "65979076779877",
                "value": 1.0,
                "unit": "medium breast",
                "nutrition_multiplier": 1.2,
                "gram_weight": 120.0,
                "index": 1,
            },
            {
                "id": "66528832593765",
                "value": 1.0,
                "unit": "cup, cooked, diced",
                "nutrition_multiplier": 1.35,
                "gram_weight": 135.0,
                "index": 2,
            },
        ],
    }


@pytest.fixture
def entry_store(tmp_path, monkeypatch):
    """Point the entry store at a temp directory."""
    monkeypatch.setattr(server, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(server, "ENTRIES_FILE", tmp_path / "entries.json")
    return tmp_path / "entries.json"


class TestSelectServingSize:
    def test_defaults_to_first_serving_when_no_unit_given(self, food):
        assert server.select_serving_size(food)["unit"] == "oz"

    def test_matches_unit_exactly(self, food):
        assert server.select_serving_size(food, "medium breast")["unit"] == "medium breast"

    def test_matching_is_case_insensitive(self, food):
        assert server.select_serving_size(food, "MEDIUM BREAST")["unit"] == "medium breast"

    def test_matching_ignores_surrounding_whitespace(self, food):
        assert server.select_serving_size(food, "  oz  ")["unit"] == "oz"

    def test_matches_on_substring(self, food):
        # "cup" should find "cup, cooked, diced"
        assert server.select_serving_size(food, "cup")["unit"] == "cup, cooked, diced"

    def test_falls_back_to_default_when_unit_unknown(self, food):
        # An unmatched unit must not raise - logging the food matters more than
        # the exact serving, and the caller is warned in the logs.
        assert server.select_serving_size(food, "furlong")["unit"] == "oz"

    def test_returns_only_fields_the_diary_api_permits(self, food):
        # MFP rejects the whole request if id/gram_weight/index are included.
        assert set(server.select_serving_size(food)) == {
            "value",
            "unit",
            "nutrition_multiplier",
        }

    def test_carries_through_the_nutrition_multiplier(self, food):
        assert server.select_serving_size(food, "medium breast")["nutrition_multiplier"] == 1.2

    def test_raises_when_food_has_no_serving_sizes(self):
        with pytest.raises(RuntimeError, match="no serving sizes"):
            server.select_serving_size({"id": "123", "serving_sizes": []})

    def test_raises_when_serving_sizes_key_is_absent(self):
        with pytest.raises(RuntimeError, match="no serving sizes"):
            server.select_serving_size({"id": "123"})


class TestEntryStore:
    def test_round_trips_an_entry(self, entry_store):
        server.remember_entry("abc-123", date="2026-07-21", meal="Lunch", description="Eggs")

        assert server.load_entry_log()["abc-123"] == {
            "date": "2026-07-21",
            "meal": "Lunch",
            "description": "Eggs",
        }

    def test_load_returns_empty_when_no_store_exists(self, entry_store):
        assert server.load_entry_log() == {}

    def test_forget_removes_only_the_named_entry(self, entry_store):
        server.remember_entry("a", date="2026-07-21", meal="Lunch", description="Eggs")
        server.remember_entry("b", date="2026-07-21", meal="Dinner", description="Steak")

        server.forget_entry("a")

        assert set(server.load_entry_log()) == {"b"}

    def test_forgetting_an_unknown_entry_is_harmless(self, entry_store):
        server.remember_entry("a", date="2026-07-21", meal="Lunch", description="Eggs")

        server.forget_entry("does-not-exist")

        assert set(server.load_entry_log()) == {"a"}

    def test_prunes_oldest_entries_past_the_cap(self, entry_store, monkeypatch):
        monkeypatch.setattr(server, "MAX_REMEMBERED_ENTRIES", 3)

        for day in range(1, 6):
            server.remember_entry(
                f"entry-{day}",
                date=f"2026-07-0{day}",
                meal="Lunch",
                description=f"Food {day}",
            )

        # The three newest survive; the two oldest are dropped.
        assert set(server.load_entry_log()) == {"entry-3", "entry-4", "entry-5"}

    def test_corrupt_store_degrades_to_empty_rather_than_raising(self, entry_store):
        entry_store.parent.mkdir(parents=True, exist_ok=True)
        entry_store.write_text("{not valid json")

        assert server.load_entry_log() == {}

    def test_store_is_not_world_readable(self, entry_store):
        server.remember_entry("abc", date="2026-07-21", meal="Lunch", description="Eggs")

        # Entry ids are not secrets, but they sit beside session cookies.
        assert entry_store.stat().st_mode & 0o077 == 0

    def test_store_is_valid_json_on_disk(self, entry_store):
        server.remember_entry("abc", date="2026-07-21", meal="Lunch", description="Eggs")

        assert json.loads(entry_store.read_text())["abc"]["meal"] == "Lunch"

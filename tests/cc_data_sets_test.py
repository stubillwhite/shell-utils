import importlib.util
import pathlib
import unittest

MODULE_PATH = pathlib.Path(__file__).parent.parent / "cc_data_sets.py"
spec = importlib.util.spec_from_file_location("cc_data_sets", MODULE_PATH)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class ParsePrefixesTest(unittest.TestCase):
    def test_extracts_prefixes_and_ignores_object_lines(self):
        listing = (
            "                           PRE 20260812-002248/\n"
            "                           PRE 20260813-002149/\n"
            "2026-07-24 00:09:45          0\n"
        )
        self.assertEqual(
            mod.parse_prefixes(listing),
            frozenset({"20260812-002248", "20260813-002149"}),
        )


class BuildTableTest(unittest.TestCase):
    def test_marks_presence_per_dataset_sorted_by_date(self):
        prefixes = {"ANI": frozenset({"b", "a"}), "APR": frozenset({"a"})}
        names, rows = mod.build_table(prefixes)
        self.assertEqual(names, ["ANI", "APR"])
        self.assertEqual(rows, [("a", ["OK", "OK"]), ("b", ["OK", "-"])])


class LatestCompleteTest(unittest.TestCase):
    def test_returns_latest_prefix_present_in_all_datasets(self):
        prefixes = {
            "ANI": frozenset({"20260813", "20260815"}),
            "APR": frozenset({"20260813", "20260815"}),
            "IHR": frozenset({"20260813"}),
        }
        self.assertEqual(mod.latest_complete(prefixes), "20260813")

    def test_returns_none_when_no_prefix_is_in_all_datasets(self):
        prefixes = {"ANI": frozenset({"a"}), "APR": frozenset({"b"})}
        self.assertIsNone(mod.latest_complete(prefixes))


class LatestAnyTest(unittest.TestCase):
    def test_returns_latest_prefix_present_in_any_dataset(self):
        prefixes = {
            "ANI": frozenset({"20260813", "20260815"}),
            "APR": frozenset({"20260813"}),
        }
        self.assertEqual(mod.latest_any(prefixes), "20260815")

    def test_returns_none_when_no_datasets_have_prefixes(self):
        self.assertIsNone(mod.latest_any({"ANI": frozenset(), "APR": frozenset()}))


class DatasetsWithTest(unittest.TestCase):
    def test_returns_names_holding_prefix_in_declaration_order(self):
        prefixes = {
            "ANI": frozenset({"p"}),
            "APR": frozenset(),
            "IPR": frozenset({"p"}),
        }
        self.assertEqual(mod.datasets_with(prefixes, "p"), ["ANI", "IPR"])


class FormatTableTest(unittest.TestCase):
    def test_narrow_headers_align_cells_to_three_wide(self):
        names = ["ANI", "APR"]
        rows = [("20260813", ["OK", "-"])]
        self.assertEqual(
            mod.format_table(names, rows),
            "Date      ANI  APR\n20260813   OK    -",
        )

    def test_wide_header_keeps_cells_aligned_beneath_it(self):
        names = ["ANI core", "APR"]
        rows = [("20260818", ["OK", "OK"]), ("20260817", ["OK", "-"])]
        self.assertEqual(
            mod.format_table(names, rows),
            "Date      ANI core  APR\n"
            "20260818        OK   OK\n"
            "20260817        OK    -",
        )


class FormatDatasetPathsTest(unittest.TestCase):
    def test_aligns_labelled_paths_using_supplied_labels_and_paths(self):
        labels = {"ANI core": "ANI core", "APR": "APR"}
        paths = {"ANI core": "s3://a/", "APR": "s3://b/"}
        self.assertEqual(
            mod.format_dataset_paths("20260818", ["ANI core", "APR"], labels, paths),
            "ANI core: s3://a/20260818/\nAPR:      s3://b/20260818/",
        )


class RenderTest(unittest.TestCase):
    def test_renders_title_table_and_latest_blocks_with_missing_dataset(self):
        prefixes = {
            "ANI core": frozenset({"20260817", "20260818"}),
            "APR": frozenset({"20260817"}),
        }
        labels = {"ANI core": "ANI core", "APR": "APR"}
        paths = {"ANI core": "s3://a/", "APR": "s3://b/"}
        self.assertEqual(
            mod.render(prefixes, labels, paths, title="Parsed Parquet"),
            "== Parsed Parquet ==\n"
            "\n"
            "Date      ANI core  APR\n"
            "20260817        OK   OK\n"
            "20260818        OK    -\n"
            "\n"
            "Latest complete data set: 20260817\n"
            "ANI core: s3://a/20260817/\n"
            "APR:      s3://b/20260817/\n"
            "\n"
            "Latest data sets: 20260818\n"
            "ANI core: s3://a/20260818/",
        )


class RenderLimitTest(unittest.TestCase):
    def _prefixes(self):
        return {
            "ANI": frozenset(
                {"20260810", "20260811", "20260812", "20260813", "20260814", "20260815"}
            ),
            "APR": frozenset({"20260810"}),
        }

    def _render(self, **kwargs):
        return mod.render(
            self._prefixes(),
            {"ANI": "ANI", "APR": "APR"},
            {"ANI": "s3://a/", "APR": "s3://b/"},
            **kwargs,
        )

    def test_limit_shows_only_the_last_n_dates_in_the_table(self):
        table = self._render(limit=5).split("\n\n")[0]
        self.assertNotIn("20260810", table)
        for date in ("20260811", "20260812", "20260813", "20260814", "20260815"):
            self.assertIn(date, table)

    def test_completeness_still_considers_dates_hidden_from_the_table(self):
        self.assertIn("Latest complete data set: 20260810", self._render(limit=5))

    def test_no_limit_shows_every_date(self):
        self.assertIn("20260810", self._render().split("\n\n")[0])


class DateLimitTest(unittest.TestCase):
    def test_default_limits_to_five_most_recent(self):
        self.assertEqual(mod.date_limit([]), 5)

    def test_all_flag_shows_every_date(self):
        self.assertIsNone(mod.date_limit(["--all"]))


if __name__ == "__main__":
    unittest.main()

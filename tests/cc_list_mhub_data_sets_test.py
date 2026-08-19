import importlib.util
import pathlib
import unittest

MODULE_PATH = pathlib.Path(__file__).parent.parent / "cc-list-mhub-data-sets.py"
spec = importlib.util.spec_from_file_location("cc_list_mhub_data_sets", MODULE_PATH)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class ConfigTest(unittest.TestCase):
    def test_enriched_group_buckets_in_order(self):
        self.assertEqual(
            mod.ENRICHED,
            {
                "ANI": "s3://sccontent-ani-parquet-prod/",
                "APR": "s3://sccontent-apr-parquet-prod/",
                "IPR": "s3://sccontent-ipr-parquet-prod/",
                "IH": "s3://sccontent-ih-parquet-prod/",
            },
        )

    def test_parsed_group_buckets_in_order(self):
        self.assertEqual(
            mod.PARSED,
            {
                "ANI core": "s3://sccontent-parsed-ani-core-parquet-prod/",
                "APR": "s3://sccontent-parsed-apr-parquet-prod/",
                "IPR": "s3://sccontent-parsed-ipr-parquet-prod/",
            },
        )

    def test_reports_both_groups_in_order(self):
        self.assertEqual(
            [title for title, _ in mod.GROUPS],
            ["Two column XOCS enriched Parquet", "Parsed Parquet"],
        )


class KeepDatesTest(unittest.TestCase):
    def test_keeps_only_eight_digit_date_folders(self):
        self.assertEqual(
            mod.keep_dates(
                frozenset({"20260818", "debug", "20260817-tmp", "2026081", "202608180"})
            ),
            frozenset({"20260818"}),
        )


if __name__ == "__main__":
    unittest.main()

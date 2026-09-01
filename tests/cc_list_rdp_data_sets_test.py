import importlib.util
import pathlib
import unittest

MODULE_PATH = pathlib.Path(__file__).parent.parent / "cc-list-rdp-data-sets.py"
spec = importlib.util.spec_from_file_location("cc_list_rdp_data_sets", MODULE_PATH)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class ConfigTest(unittest.TestCase):
    def test_datasets_include_daily_ani_variants(self):
        self.assertEqual(
            mod.DATASETS,
            {
                "ANI core": "s3://sccontent-prod-corecomplete-xocs-us-east-2/prod/xocs/ANI/Core/output/three_column/",
                "ANI dummy": "s3://sccontent-prod-corecomplete-xocs-us-east-2/prod/xocs/ANI/Dummy/output/three_column/",
                "ANI non-scopus-publication": "s3://sccontent-prod-corecomplete-xocs-us-east-2/prod/xocs/ANI/Non-scopus-publication/output/three_column/",
                "ANI preprint": "s3://sccontent-prod-corecomplete-xocs-us-east-2/prod/xocs/ANI/Preprint/output/three_column/",
                "APR": "s3://sccontent-prod-corecomplete-xocs-us-east-2/prod/xocs/APR/output/three_column/",
                "IHR": "s3://sccontent-prod-corecomplete-xocs-us-east-2/prod/xocs/IHR/output/three_column/",
                "IPR": "s3://sccontent-prod-corecomplete-xocs-us-east-2/prod/xocs/IPR/output/three_column/",
            },
        )

    def test_labels_match_dataset_names(self):
        self.assertEqual(
            mod.LABELS,
            {
                "ANI core": "ANI core",
                "ANI dummy": "ANI dummy",
                "ANI non-scopus-publication": "ANI non-scopus-publication",
                "ANI preprint": "ANI preprint",
                "APR": "APR",
                "IHR": "IHR",
                "IPR": "IPR",
            },
        )


if __name__ == "__main__":
    unittest.main()

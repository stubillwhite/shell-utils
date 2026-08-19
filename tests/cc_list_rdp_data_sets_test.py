import importlib.util
import pathlib
import unittest

MODULE_PATH = pathlib.Path(__file__).parent.parent / "cc-list-rdp-data-sets.py"
spec = importlib.util.spec_from_file_location("cc_list_rdp_data_sets", MODULE_PATH)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class DatasetLabelTest(unittest.TestCase):
    def test_includes_path_segment_between_name_and_output(self):
        self.assertEqual(mod.dataset_label("ANI"), "ANI core")

    def test_is_just_the_name_when_no_extra_segment(self):
        self.assertEqual(mod.dataset_label("APR"), "APR")


class LabelsTest(unittest.TestCase):
    def test_labels_are_derived_for_every_dataset(self):
        self.assertEqual(
            mod.LABELS,
            {"ANI": "ANI core", "APR": "APR", "IHR": "IHR", "IPR": "IPR"},
        )


if __name__ == "__main__":
    unittest.main()

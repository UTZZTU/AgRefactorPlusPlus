import importlib
import unittest


class PackageImportTests(unittest.TestCase):
    def test_stage1_packages_import(self) -> None:
        modules = [
            "agrefactor",
            "agrefactor.config",
            "agrefactor.models",
            "agrefactor.evaluation",
            "agrefactor.runtime",
        ]
        for module_name in modules:
            with self.subTest(module=module_name):
                importlib.import_module(module_name)


if __name__ == "__main__":
    unittest.main()

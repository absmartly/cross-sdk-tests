"""Keep independently packaged Dart/Flutter assertions in sync without build coupling."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent.parent
DART_IMPORT = "import 'package:absmartly_dart/absmartly_dart.dart';"
FLUTTER_IMPORT = "import 'package:absmartly_sdk/absmartly_sdk.dart';"


class ProbeParityTests(unittest.TestCase):
    def test_lane_probes_only_differ_in_import(self):
        # Each lane tests its own public SDK package; only the copy note and that import may differ.
        for name in ("holdout_self_test.dart", "switchback_self_test.dart"):
            with self.subTest(probe=name):
                dart = (ROOT / "dart-wrapper" / name).read_text().splitlines()
                flutter = (ROOT / "flutter-wrapper" / "lib" / name).read_text().splitlines()
                self.assertEqual(flutter[0], f"// Copy of dart-wrapper/{name}; only the import differs.")
                self.assertEqual(dart.count(DART_IMPORT), 1)
                self.assertEqual(flutter.count(FLUTTER_IMPORT), 1)
                self.assertNotIn(DART_IMPORT, flutter)
                self.assertEqual([DART_IMPORT if line == FLUTTER_IMPORT else line for line in flutter[1:]], dart)


if __name__ == "__main__":
    unittest.main()

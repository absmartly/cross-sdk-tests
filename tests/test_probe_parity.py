"""Keep independently packaged Dart/Flutter assertions in sync without build coupling."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent.parent


class ProbeParityTests(unittest.TestCase):
    def test_lane_probes_only_differ_in_import(self):
        for name in ("holdout_self_test.dart", "switchback_self_test.dart"):
            with self.subTest(probe=name):
                dart = (ROOT / "dart-wrapper" / name).read_text()
                flutter = (ROOT / "flutter-wrapper" / "lib" / name).read_text()
                flutter = "\n".join(flutter.splitlines()[1:]) + "\n"
                flutter = flutter.replace("package:absmartly_sdk/absmartly_sdk.dart",
                                          "package:absmartly_dart/absmartly_dart.dart")
                self.assertEqual(dart, flutter)


if __name__ == "__main__":
    unittest.main()

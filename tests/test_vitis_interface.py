from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from agrefactor.vitis_interface import discover_top_io, parse_top_io


XML = """<root><kernel src_name="top_hls"><args>
<arg id="0" src_name="height" src_type="int const *" src_isptr="1"
 src_bitwidth="32" src_size_or_depth="12">
 <hw hw_interface="MAXI" hw_name="gmem" />
</arg>
<arg id="1" src_name="count" src_type="int" src_isptr="0"
 src_bitwidth="32"><hw hw_interface="S_AXILITE" hw_name="count" /></arg>
</args></kernel></root>"""


class VitisInterfaceTests(unittest.TestCase):
    def test_parses_final_vitis_interface(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "top-io-fe.xml"
            path.write_text(XML, encoding="utf-8")
            interface = parse_top_io(path, expected_top="top_hls")
            self.assertIsNotNone(interface)
            assert interface is not None
            self.assertEqual(interface.maxi_pointer_ports, ("height",))
            self.assertEqual(interface.ports[0].size_or_depth, 12)

    def test_discovers_one_interface_and_treats_bad_xml_as_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "solution" / ".autopilot" / "db" / "top-io-fe.xml"
            path.parent.mkdir(parents=True)
            path.write_text(XML, encoding="utf-8")
            self.assertIsNotNone(discover_top_io(root, expected_top="top_hls"))
            path.write_text("not xml", encoding="utf-8")
            self.assertIsNone(discover_top_io(root, expected_top="top_hls"))


if __name__ == "__main__":
    unittest.main()

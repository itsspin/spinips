"""Check native loadout hierarchy and race/gender selection hit regions."""
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def check(skin):
    root = ET.parse(skin / "EQUI_LoadoutWnd.xml").getroot()
    items = {node.get("item"): node for node in root if node.get("item")}
    screen = items["LOW_RaceScreen"]
    pieces = [node.text for node in screen.findall("Pieces")]
    assert "LOW_Race_Subwindows" in pieces, "Race screen must host the gender tabs"
    assert not any("Overlay" in name for name in pieces), "Old overlays intercept race clicks"
    tabs = items["LOW_Race_Subwindows"]
    pages = [node.text for node in tabs.findall("Pages")]
    assert pages == ["LOW_Race_Male_Page", "LOW_Race_Female_Page"], pages
    found = set()
    for name in pages:
        page = items[name]
        assert page.findtext("TabText") in ("Male", "Female")
        buttons = [items[node.text] for node in page.findall("Pieces")]
        assert len(buttons) == 16
        boxes = []
        for button in buttons:
            assert button.tag == "Button"
            assert button.get("item") not in found
            found.add(button.get("item"))
            x, y = int(button.findtext("Location/X")), int(button.findtext("Location/Y"))
            w, h = int(button.findtext("Size/CX")), int(button.findtext("Size/CY"))
            assert x >= 0 and y >= 0 and x + w <= 198 and y + h <= 210
            assert all(x+w <= a or a+c <= x or y+h <= b or b+d <= y for a,b,c,d in boxes)
            boxes.append((x,y,w,h))
    assert len(found) == 32
    print(f"{skin.name}: PASS | two gender pages, 32 non-overlapping race controls")


if __name__ == "__main__":
    for name in ("spinui_reloaded", "spinui_glass", "spinui_pearlescent"):
        check(ROOT / name)

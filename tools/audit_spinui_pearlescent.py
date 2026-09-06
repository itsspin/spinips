"""Validate the generated Pearl skin without relaxing gameplay parity."""
import subprocess
import sys
import audit_spinui_glass as audit
from restyle_combat import ATTACK_RAIL_TEXTURE, ATTACK_PERIMETER_TEXTURE
from PIL import Image


def main():
    audit.GLASS = audit.REPO / "spinui_pearlescent"
    audit.CONTROL_ATLAS = "spin_pearl_controls.tga"
    subprocess.run([sys.executable, str(audit.REPO / "tools/build_spinui_pearlescent.py"), "--check"], check=True)
    files, bindings = audit.check_xml_parity()
    for name in audit.REQUIRED_DIFFERENCES:
        with Image.open(audit.GLASS / name) as pearl, Image.open(audit.SOURCE / name) as original:
            assert pearl.size == original.size, name
            assert pearl.tobytes() != original.tobytes(), name
    # Attack textures must not be recolored or attenuated by decorative chrome.
    for name in (ATTACK_RAIL_TEXTURE, ATTACK_PERIMETER_TEXTURE):
        source = audit.SOURCE / name
        assert source.read_bytes() == (audit.GLASS / source.name).read_bytes(), source.name
    print(f"Pearlescent parity: PASS | {files} XML files, {bindings} bindings")


if __name__ == "__main__":
    main()

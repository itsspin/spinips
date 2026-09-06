"""Render Pearlescent from the generated native skin atlases."""
import render_preview as ui
import build_spinui_pearlescent as pearl
import render_equipment_preview as equipment


if __name__ == "__main__":
    pearl.configure()
    tokens = pearl.builder.texture_tokens()
    ui.SKIN = pearl.builder.OUTPUT
    ui.OUTPUT_BASENAME = "spinui_pearlescent"
    ui.PREVIEW_SUBTITLE = "SpinUI Pearlescent - black pearl, ivory and seafoam"
    ui.TEX.clear()
    for name in ("BG1", "BG2", "BG3", "CYAN", "EMBER", "GOLD", "GOLD_BRIGHT",
                 "LINE", "LINE_SOFT", "TEXT", "TEXT_DIM", "VOID"):
        setattr(ui, name, tokens[name])
    ui.DIM = pearl.builder.FROST_DIM
    ui.PARCHMENT = pearl.builder.FROST
    ui.main()
    equipment.SKIN = pearl.builder.OUTPUT
    equipment.OUTPUT_FILENAME = "spinui_pearlescent_equipment.png"
    for name in ("BG1", "BG2", "CYAN", "EMBER", "GOLD", "GOLD_BRIGHT",
                 "LINE", "LINE_SOFT", "TEXT", "TEXT_DIM"):
        setattr(equipment, name, tokens[name])
    equipment.DIM = pearl.builder.FROST_DIM
    equipment.main()

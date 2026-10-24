# skate-3-skater-maker
Python CLI and Tkinter GUI for editing Skate 3 (PS3) SKATER.P save files: fix CRC, delete gear slots, swap LOGO IDs, replace custom graphics with your own images, and recolour gear with optional neon/glow.

# Skate 3 SKATER.P Editor

A reverse-engineered toolkit for inspecting and patching Skate 3 (PS3) `SKATER.P` save files, with a command-line tool and a Tkinter GUI.

## Features
- **CRC check and repair**: recalculates the CRC-32/BZIP2 checksum so edited saves are accepted by the game
- **Slot editor**: list and delete gear/body slots, with the nested size fields repaired automatically
- **Custom graphics**: export embedded graphics as PNG, fill them with a solid colour, or replace them with your own image (resize, fit/fill/stretch, sharpen, tone correction)
- **LOGO ID swap**: change the user-content IDs referenced by a skater
- **Colour editor**: change the tint of any gear piece, with glow and neon modes and presets
- **Hex patch and Compare**: patch raw bytes and diff two saves, with changed bytes labelled by gear slot

## Usage
    python skater_tool.py info SKATER.P
    python skater_tool.py setgraphic SKATER.P 89204405 picture.png -o out.P
    python skater_gui.py

Keep `skater_gui.py` in the same folder as `skater_tool.py`. Image import need Pillow (`pip install pillow`).

## Limitations
Length-changing edits (adding gear entries, swapping in a different-sized JPEG) aren't supported. **Always back up your original save.**

## Disclaimer
Unofficial fan project, not affiliated with EA or Black Box.

Yes ts is vibe coded with claude

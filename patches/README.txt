Translation-only patch package; use the exact supported game versions listed in manifest.json.
For each xdelta3 entry, run: xdelta3 -d -s <source_path> <patch_path> <output>.
GC: place the decoded GZLE01.iso in your BlueWake disc location or Switch SD switch/switchwaker/.
HD: decode the content/ paths into a separate overlay folder, never over the original game.
Cemu: place that content/ folder inside graphicPacks/Picoripi_UA_WWHD/, beside rules.txt copied
from hd/graphicPacks/Picoripi_UA_WWHD/rules.txt. Enable the Picoripi UA graphic pack in Cemu.
Wii U: place content/ under SD wiiu/sdcafiine/0005000010143500/Picoripi_UA/.
SwitchWakerHD: copy content/ over SD switch/wwhd/game/content/ (keep an original backup).
See manifest.json for SHA256 values and relative paths.
This package does not include game images, source files, executables, keys, or saves.

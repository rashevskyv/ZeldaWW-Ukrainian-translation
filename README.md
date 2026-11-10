# The Wind Waker — Ukrainian translation

Translation tooling and patches for The Legend of Zelda: The Wind Waker.

The primary Windows target is BlueWake, the static recompilation of the GameCube USA revision 0 release (GZLE01). Additional targets are Dolphin, SwitchWaker, Wind Waker HD in Cemu, Wii U SDCafiine, and SwitchWakerHD.

Run `Play Ukrainian.bat` in the Wind Waker workspace to build the saved translation and launch BlueWake.
The workflow stages the family export and both builds before it replaces the generated `build` folders.
For each shared GameCube archive, it selects the newer saved copy from the GC project and family project.
Conflicting files with identical save times stop the build. It keeps HD-only family translations. It does not
change the Picoripi projects, translation folders, original images, or saves. Before replacing build output,
it verifies the SwitchWaker disc ID, GameCube magic, and original `main.dol` hash required by BlueWake.

Use `Build All.bat` for GameCube and HD outputs. `Play Dolphin.bat` and `Play Cemu.bat` build the matching
target before launch. `Build SwitchWaker.bat` and `Build Wii U and SwitchWakerHD.bat` prepare their files
for copying to the relevant SD card. The old xdelta `patches/` folder is gone: the translation is published
into `patcher/files` by Picoripi (Build ROM, Build mod or Run in emulator of the Wind Waker project).

Game images, extracted original game files, port binaries, keys, local settings, and saves stay outside this repository. Obtain the game data from your own copy.

Upstream port: https://github.com/chrissotraidis/bluewake

TP-style patcher for your own GameCube disc (USA, GZLE01): `patcher/` holds the changed files, `manifest.json` and
`patcher.py`; it downloads this repository, rebuilds your .iso/.rvz into `<name>_UA.iso` and never touches the original.
Instructions in Ukrainian: `patcher/README.md`.

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
for copying to the relevant SD card. `Publish Translation.bat` builds translation-only xdelta patches and
publishes them to this repository's `origin`; use `python publish_translation.py --prepare-only` to prepare
the package for review without staging, committing, or pushing. The publisher rejects unrelated dirty files
and rejects a remote that does not match this repository. It reads xdelta3 and Wind Waker HD paths from the
shared `zelda_env.ini`. Set `WINDWAKER_TRANSLATION_REPO` if this repository is outside its default location
(`D:\git\dev\ZeldaWW-Ukrainian-translation`).

Game images, extracted original game files, port binaries, keys, local settings, and saves stay outside this repository. Obtain the game data from your own copy.

Upstream port: https://github.com/chrissotraidis/bluewake

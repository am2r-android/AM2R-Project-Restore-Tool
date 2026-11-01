AM2R Project Restore Tool 0.2.0

PRT is the short name used for the executable and download filenames.

Restore missing assets in published Standard and Dual Screen GameMaker projects
using your original, unmodified AM2R 1.1 ZIP. This is a separate desktop developer
utility, not the Android Autopatcher. It produces editable assets, not an APK.
It does not download games, replace source code or upgrade an older release.

The tool has its own version number, independent of AM2R. Its offline catalog
supports every game-source archive attached to the Android Autopatcher releases
from 1.5.5.9 through 1.5.5.14, including the published betas and hotfixes.
SUPPORTED-RELEASES.txt lists the exact 22 supported archives and their checksums.
Earlier repository tags contain Autopatcher code, not GameMaker game projects.
Future releases need an updated catalog when their source manifest is new; that
need not require changes to the restoration program itself.

Quick start
1. Extract the entire Windows or Linux portable download.
2. Download the Standard or Dual Screen game-source ZIP for your desired release:
   https://github.com/am2r-android/AM2R-Autopatcher-Android/releases
   Use the attached AM2R-...-standard-source.zip or AM2R-...-dual-source.zip.
   The Autopatcher source ZIP and GitHub automatic Source code downloads are
   different projects and are not inputs to this tool.
3. Extract the game-source ZIP to its own folder. Keep asset-inputs.json intact.
4. Launch AM2R-PRT.exe on Windows or AM2R-PRT on Linux.
   Select your AM2R 1.1 ZIP and the extracted AM2R-project folder. Choose Restore.
   The tool selects the matching assets automatically, including for .12 or .13.
5. Open AM2R-project.yyp in GameMaker. Follow that release's BUILDING.txt and
   runtime integration instructions. Older instructions describing only the
   private-donor helper predate this utility; build requirements still apply.

Portable application requirements
Keep _internal, tools and bundle beside the executable. Python, Tcl/Tk, xdelta3
and their required notices are included; no separate installation is necessary.
Linux x86_64 requires glibc 2.36 or newer, X11 or XWayland, and normal desktop
font configuration. It is not an ARM executable. Windows is also x86_64.
Use a local filesystem supporting hard links, such as NTFS, ext4 or Btrfs.
Allow roughly 2 GiB of working space per project, in addition to the tool.
GameMaker, its licensed runtime, Android tools and your signing key are separate.
Platform checks and limitations are in VALIDATION.txt beside the downloads.

Preservation and verification
The original data.win, patch, reconstructed payload and every restored input
are checked by size and SHA-256. The exact published asset manifest selects the
catalog entry. An unknown or edited manifest is rejected, rather than silently
using the latest assets. No source manifest is rewritten. Code edits are
reported and preserved; missing source files stop restoration.
Existing matching assets are skipped. A different existing asset stops the
operation before missing assets are written. Move your edited asset yourself
if you intend to restore the original. Files appearing during restoration are
never overwritten. After an interruption, rerun to fill in remaining assets.
Identical sprite/layer files remain independently editable.

Some older releases share an identical asset set. The report lists compatible
releases and uses the closest matching source as its reference for detecting
code edits. Identical archives attached to two releases share that reference.
This selection never changes project code or which assets are restored.
Legacy manifests also listed local import reports and an internal build marker.
Reports receive a clean explanatory note, preserving their note resources. The
build marker is omitted. Both are reported explicitly and have no gameplay role.
Artwork, audio, fonts and language inputs match the selected release's hashes.
Complete local donor projects are never modified.

The tool checks completeness, not whether later code edits compile. It does not
promise identical build timestamps or signatures. Use your own signing key and
separate app ID for development. The download has no release signing key,
proprietary runtime, saved game, full game APK or complete restored asset pack.

Command line
The portable executable accepts these arguments. Use --report with the Windows
windowed executable when launching from a terminal:
  AM2R-PRT --zip /path/to/AM2R_11.zip --project /path/to/AM2R-project
  AM2R-PRT --project /path/to/AM2R-project --check --report check.json
  AM2R-PRT --version
Reports need a new filename. --check is read-only. The catalog defaults to
bundle/ beside the app; --bundle can select another catalog or an original
single-release bundle. An older bundle requires its original matching schema-2
source manifest.

Running or maintaining the Python source
The source archive needs Python 3.11+, tkinter for the GUI and xdelta3 on PATH,
or in tools/ beside restore_project.py. Use python3 restore_project.py with the
same arguments. The portable download supplies the offline bundle data; the
small program source archive alone does not include it.
Run python3 -m unittest discover -s tests -v for behavior checks.
build_catalog.py builds a deduplicated catalog from checksum-verified public
archives, a reviewed archive list, complete local asset donors and the original
1.1 ZIP. catalog-inputs.json is this version's list. --help lists arguments.
The builder verifies an actual patch decode. build_bundle.py remains available
to export future source snapshots and their original single-release bundles.
Neither builder substitutes similar assets for missing exact inputs.

Build portable programs on their target OS with packaging/requirements.txt and
packaging/build_portable.py. Build Linux with packaging/Dockerfile and Windows
in Windows. Supply the catalog kit, verified native decoder and dependency
licenses. Preserve executable permissions and symbolic links in Linux archives.
Test the completed application without a system Python or xdelta3.

Licensing
LICENSE covers this utility, not AM2R or its assets. Inherited game code and
third-party notices retain the conditions in each source archive. Restored media
retain their original rights; do not redistribute complete restored assets or
game builds as part of a source upload. Required author notices are preserved.
GMXDataSync is the model for restoring owned-game assets into source; this tool
targets modern YYP and does not copy its implementation.
https://github.com/YAL-GameMaker-Tools/GmxDataSync

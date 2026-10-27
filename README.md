# premiere-to-resolve (Vers DaVinci)

> **Unsigned development extension.** This Adobe CEP panel is not signed or notarised. It runs
> only if CEP PlayerDebugMode is already enabled on your machine. This package does not change
> that setting. A public distribution would need a signature first.

A Premiere Pro panel for macOS that round-trips a sequence through DaVinci Resolve Studio for
colour grading:

1. **Outbound**: export the active sequence as Final Cut Pro 7 XML, write a colour-free copy of it,
   create a new colour project in Resolve, import the referenced source files and open the Colour page.
2. **Return**: render the graded shots from Resolve, then import them back into Premiere as a new sequence in a new bin.

The original sequence is never modified or replaced. There is no automatic two-way sync: each
step is an explicit button press.

## Requirements

- macOS.
- Premiere Pro 25 or later (the version the author used; not re-tested for this package).
- Python 3.
- DaVinci Resolve **Studio**. The free version does not expose the local scripting API.
- In Resolve: Preferences > System > General > External scripting using = **Local** (not Network).

## Installation

Copy the folder into the CEP extensions directory:

```sh
cp -R premiere-to-resolve ~/Library/Application\ Support/Adobe/CEP/extensions/com.sevenxdev.premieretoresolve
```

Restart Premiere if the panel does not appear, then open **Window > Extensions > Vers DaVinci**.
Save your project before you use the panel.

The bundle identifier is `com.sevenxdev.premieretoresolve`. It differs from any earlier private
install, so both can coexist.

## What the outbound button does

1. Starts Resolve if needed and checks the local scripting connection before any export.
2. Exports the active sequence as Final Cut Pro 7 XML.
3. Keeps that original and writes a separate copy. The copy removes only recognised colour filters,
   LUTs and video generators. Positions, audio, transforms, frame rate and timecode are kept.
4. Checks the local media paths. Every referenced source file must be online.
5. Creates a new colour project, imports the source files, selects the timeline, saves it and opens the Colour page.
6. Compares the shot count and paths with the XML and reports any difference. It does not claim the conform is validated.

Each run writes a folder `~/Movies/Vers DaVinci/COLOR-...` with the original XML, the colour XML,
the source information and a report. The panel lists these transfers, newest first.

## What the return button does

1. Reads the chosen transfer and checks that the open Resolve project and timeline are exactly those of the outbound run. If not, nothing starts.
2. Refuses to start if another render has begun. The panel never stops a render it did not start.
3. Loads the Premiere XML render preset, switches to individual clips and renders MOV ProRes 422 HQ.
4. Renders into a new `return-...` folder, so no existing file is overwritten.
5. Follows the render and shows its progress. The wait is capped at 30 minutes; on timeout the panel stops following and leaves the render running in Resolve.
6. Checks the XML that was produced: one XML file, and every video clip points to a MOV in the return folder.
7. Imports into a new Premiere bin, checks that the media comes from the return and that the shot count matches the outbound transfer, then opens the renamed sequence.

A transfer that was already processed is refused. Start a new transfer from the button to make a new copy.

The XML does not carry colour settings: they are applied inside the rendered clips. Check cuts,
transitions, audio and framing before delivery.

## Limits

- Nested sequences, multicam sequences and speed changes are refused with a message. Flatten them in Premiere and export again.
- Third-party effects, Dynamic Link, transitions and media interpretation are not guaranteed by the XML. No intermediate render is made automatically.
- Adjustment layers and some titles exported as clips without media may be refused rather than silently dropped.
- The colour copy exists in Resolve and in the XML, not as a second sequence in Premiere.
- No transcoding and no copy of the rushes: Resolve points to the original files. No proxy is generated.
- No camera profile, technical LUT or colour space is guessed. Check the input colour space and colour management in Resolve before grading.
- The shot-count check does not replace a visual comparison of cuts, framing and transitions.
- The return needs Resolve Studio open, with the outbound project and timeline open and active.
- Resolve does not lock manual changes to the project or timeline during a render. The panel re-checks before each step and stops at the first difference, without stopping any render.
- The code makes no network call. It runs locally with no background service and no subscription.
- The panel's interface text is in French.

## Tests

```sh
sh tests/controles.sh
```

This runs:

- Python tests of the bridge (`test_prepare_xml.py`, `test_send.py`, `test_return_grade.py`), with synthetic data only.
- Node tests of the panel and of the Premiere host script (`panel_test.cjs`, `host_test.cjs`), against fake Premiere and DOM objects.
- A syntax check of the panel, the host script and the manifest.

No test starts Premiere or Resolve. The tests that drove the real applications were development probes
on the author's machine and are not part of this package. The end-to-end run in Premiere and Resolve
has not been reproduced for this package.

## Licence

MIT. See `LICENSE`.

# OW Scoreboard Capture

A small Windows app for contributing end-of-match scoreboard screenshots to the
Overwatch 6v6 scoreboard parser. Press a hotkey on the scoreboard, and when
you've collected a few, click **Package for sending** and send the zip file.

## For contributors

1. Download `OWScoreboardCapture.exe` from the latest release and run it. No
   install needed.
   - Windows may say *"Windows protected your PC"*, because the app isn't
     code-signed. Click **More info → Run anyway**.
2. In Overwatch, set **Options → Video → Display Mode** to **Borderless
   Windowed**. In exclusive Fullscreen, Windows can't capture the game and
   you'll get black images (the app warns you if that happens).
3. At the **end of a match**, when the full scoreboard is showing (both teams,
   the stats and the hero panel), press the hotkey. The default is **F8**; you
   can change it in the app. You'll hear a beep and see the saved file name.
4. When you have some captures, click **Package for sending**. A zip file
   opens in Explorer; send it however you were asked to (Discord, Drive,
   email). Packaged captures move to a `sent` folder, so the next package only
   has new ones.

Tips:
- Keep the app open while you play. It sits in the background, and the hotkey
  works while the game has focus.
- If you have more than one monitor, pick the one Overwatch is on.
- Don't choose F9 or F10: Overwatch uses them on the scoreboard screen. A
  hotkey claimed by this app no longer reaches the game.

### What gets captured and sent
The whole monitor you selected, exactly as shown: the scoreboard, including
other players' names, plus anything else on that screen (chat, overlays). The
zip also contains `info.txt` with the app version, your optional name, the
number of captures, and your monitor resolutions. Nothing is uploaded
automatically; you decide what to send.

### Where files go
By default: `Pictures\OW Scoreboard Captures\` (you can change it with
**Browse…**). Zips are in its `packages\` subfolder. Settings are stored in
`%APPDATA%\OWScoreboardCapture\settings.json`.

## For the maintainer

Build the exe (Windows, Python 3.10+):

```
powershell -ExecutionPolicy Bypass -File capture\build.ps1
```

This installs Pillow and PyInstaller if needed and writes
`capture\dist\OWScoreboardCapture.exe` (build outputs are git-ignored). Attach
that file to a GitHub release.

Design notes:
- The hotkey uses the Win32 `RegisterHotKey` API, the same mechanism ShareX
  and OBS use. There is no keyboard hook, and it never reads or injects game
  input.
- The process is per-monitor DPI aware, so captures are full native
  resolution under Windows display scaling.
- Captures are lossless PNG, named `scoreboard_YYYY-MM-DD_HH-MM-SS.png`.

# Interpres (ClipTranslate)

A background translation app for Linux which captures user command (**Ctrl+C twice**) within 500 ms and a popup near the pointer translates the copied text.

![Interpres demo: double Ctrl+C opens the popup, then the target language is changed](docs/demo.gif)

> The keystrokes are observed rather than intercepted, so the source app still performs its normal copy operation.

## Install on Arch / Omarchy

Build and install the package from this repo (needs `git` and `uv`):

```bash
git clone https://github.com/Sloppy1999/interpres.git
cd interpres/packaging
makepkg -si
systemctl --user enable --now interpres
```

Then add the Hyprland binding from the Wayland section below, using the installed command `interpres-trigger`. The service starts the app on login.

## Setup
Install Python 3.12+, [uv](https://docs.astral.sh/uv/), and Git. From this directory:

```bash
uv sync
uv run interpres
```

The default provider is **local** usesing [Argos Translate](https://github.com/argosopentech/argos-translate) for translation and [py3langid](https://github.com/adbar/py3langid) for language detection. The first use of a language pair downloads and installs its model pack and any needed sentence model; the popup shows these steps. Once installed, that pair translates offline, and selected text is never sent to a translation service. For another machine with no internet access, copy the Argos data directory, including its `packages` and `minisbd` subdirectories.

### Optional DeepL provider

For the Deepl option get an API key from: [DeepL API account](https://www.deepl.com/pro-api) and then set `provider = "deepl"` in `~/.config/cliptranslate/config.toml` (or `$XDG_CONFIG_HOME/cliptranslate/config.toml`).

## Known limitations

- Windows and macOS are not supported for now.
- On X11 the global hook works directly. On Wayland only Hyprland is covered, through the binding above; other compositors need their own integration.
- Short text can be misidentified by the local language detector.
- Some pairs use English as an intermediate language, which can reduce quality.
- Local models are downloaded over HTTPS from the public Argos package index, which publishes no checksums, so they are not integrity-checked.

## License

MIT, see [LICENSE](LICENSE)

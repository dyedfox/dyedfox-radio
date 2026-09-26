# Translating Dyedfox Radio

Translations use the Qt Linguist format (`.ts` / `.qm`).

## Adding a new language

1. Copy `dyedfox-radio_uk.ts` and rename it to `dyedfox-radio_<locale>.ts`  
   (e.g. `dyedfox-radio_de.ts` for German, `dyedfox-radio_pl.ts` for Polish).
2. Set the `language` attribute in the `<TS>` tag to the target locale  
   (e.g. `language="de_DE"`).
3. Fill in the `<translation>` elements for each `<message>`.
4. Submit a pull request — thank you!

## Updating strings after a code change (developers)

Run `pylupdate6` from the project root to refresh the `.ts` files:

```
pylupdate6 \
    ui/main_window.py ui/station_list.py ui/info_panel.py ui/about_dialog.py \
    ui/settings_dialog.py ui/controls.py ui/now_playing.py ui/add_station_dialog.py \
    ui/strings.py \
    tray/tray_icon.py \
    -ts translations/dyedfox-radio_uk.ts
```

Existing translations are preserved; new source strings are added with  
`type="unfinished"`.

## How the app loads translations (developers)

At startup the app reads the system locale (e.g. `uk_UA`) and loads  
`translations/dyedfox-radio_uk_UA.qm` or `translations/dyedfox-radio_uk.qm`  
if present. If neither file exists the UI stays in English.

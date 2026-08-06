# Inventory Rebuy Review Dashboard

A private local Streamlit app for reviewing bi-weekly inventory rebuy requests one SKU at a time from the `REBUYS` tab of an Excel workbook.

The app is designed for the workbook structure found in `BEAUTY REBUYS 6-9-26 PreMeeting.xlsx`, while still detecting campaign columns dynamically so future bi-weekly files can be loaded without changing code.

## What this app does

- Loads a rebuy Excel workbook locally.
- Reads the main `REBUYS` tab, where each row is one SKU/rebuy request.
- Detects future demand campaign columns dynamically from the `REBUYS` header row.
- Reads `ACTUAL SALES` and detects campaign columns dynamically from row 2.
- Reads `CALENDAR` to map campaign numbers to US campaign start dates.
- Reads `CM SKU` to flag related old/new FSCs when mappings exist.
- Lets you filter by Planner, Category, Profile #, and ABC tier.
- Lets you search by FSC or product name.
- Shows one SKU/request at a time in a clean vertical layout.
- Stores Finance comments locally in SQLite.
- Exports a comment CSV.
- Creates a new Excel workbook copy with saved Finance comments written back into `Initial Finance Comments` in the `REBUYS` tab.

## Privacy

This app runs locally on your computer. It does not send your workbook or comments to external servers.

Files uploaded through the app are stored in:

```text
app_data/uploads/
```

Comments are stored locally in:

```text
app_data/comments.db
```

Exported reviewed workbooks are stored locally in:

```text
app_data/exports/
```

## Access passcode

The dashboard is protected by a passcode screen. The passcode is intentionally
not stored in this public repository.

For local development, copy `.streamlit/secrets.toml.example` to
`.streamlit/secrets.toml` and set `APP_PASSCODE` to the desired value. The real
secrets file is excluded from Git.

For Streamlit Community Cloud, open the deployed app's settings, select
**Secrets**, and add:

```toml
APP_PASSCODE = "your-passcode"
```

## First-time setup

### Option A: Windows double-click

1. Install Python from python.org if you do not already have it.
2. Unzip this folder.
3. Double-click `run_app.bat`.
4. Your browser should open the app automatically.

### Option B: Manual setup

Open Command Prompt / Terminal in this folder, then run:

```bash
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

## How to use every two weeks

1. Start the app.
2. Upload the new bi-weekly Excel workbook, or enter its local file path.
3. Use filters/search to narrow down SKUs.
4. Review one SKU at a time.
5. Enter your Finance comment and click **Save locally**.
6. Use **Download comments CSV** if you only want a comment table.
7. Use **Create Excel copy with comments written to Initial Finance Comments** to generate a new workbook with comments written back into the `REBUYS` tab.

The original workbook is not modified. The app creates a separate reviewed copy.

## Important workbook assumptions

The app expects these sheets and structures:

### REBUYS

- Header row: row 1
- Data starts: row 2
- FSC/SKU field: `FSC#`
- Finance comment field: `Initial Finance Comments`
- Future demand campaign columns: any row 1 headers that look like campaign numbers, such as `202612`, `202613`, etc.

### ACTUAL SALES

- FSC is in column A.
- Campaign numbers are detected from row 2.
- Sales data starts on row 4.

### CALENDAR

- Campaign number is read from column B.
- US start date is read from column C.
- Meeting/current date is read from G2 when available.

### CM SKU

- Old FSC/SKU is read from column A.
- New CM SKU is read from column C.
- If this tab is blank, the app simply does not show CM flags.

## Renaming labels

To make workbook headers easier to read in the app, edit:

```text
config/display_labels.json
```

This changes display labels only. It does not change the original Excel workbook headers.

## Notes and limitations

- Formula cells are read using the values cached in Excel. If values look blank or stale, open the workbook in Excel, save it, and upload again.
- Export-back-to-Excel uses the original row number and FSC match to avoid writing comments to the wrong row.
- The app preserves the original workbook as much as possible, but it creates a new copy instead of changing the source workbook.
- Do not publicly deploy this app with company workbooks unless approved by company IT.

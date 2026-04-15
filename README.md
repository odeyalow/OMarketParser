# OMarket Selenium Parser

Simple Python parser built with `selenium` for `https://omarket.kz/`.

What it does:

- opens the OMarket homepage in Chrome
- waits until the page is ready
- reads client-side data from `window.__data`
- extracts page title, current URL, and popular categories
- prints the result as JSON

## Setup

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

The script uses Selenium Manager, so you do not need to install `chromedriver` manually.
Google Chrome must be installed on the machine.

## Mini Frontend

Run the local web UI:

```powershell
.\run_ui.bat
```

Then open `http://127.0.0.1:5050` if the browser did not open automatically.

The frontend gives you:

- an input for a search query
- a button that opens OMarket in Chrome
- automatic typing into the OMarket search field

## Run

One-command launch from PowerShell:

```powershell
.\run.bat
```

One-command headless launch:

```powershell
.\run.bat --headless
```

The first start will create `.venv` and install dependencies automatically.

Open the site in a visible browser window:

```powershell
python omarket_parser.py
```

Run in headless mode:

```powershell
python omarket_parser.py --headless
```

Save the result to a file:

```powershell
python omarket_parser.py --headless --output result.json
```

Limit the number of categories:

```powershell
python omarket_parser.py --headless --limit 5
```

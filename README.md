# Qatar Visa Center Appointment Bot

Automated appointment booking system for Qatar Visa Center portal.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    HYBRID ARCHITECTURE                       │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌──────────────┐     ┌──────────────┐     ┌─────────────┐ │
│  │   Phase 1    │     │   Phase 2    │     │   Phase 3   │ │
│  │   "TANK"     │────▶│   "SNIPER"   │────▶│  "EXECUTE"  │ │
│  │              │     │              │     │             │ │
│  │  • Browser   │     │  • Extract   │     │  • Browser  │ │
│  │  • Login     │     │    session   │     │    clicks   │ │
│  │  • CAPTCHA   │     │  • API poll  │     │  • Confirm  │ │
│  │  • Forms     │     │  • 0.5 req/s │     │  • Book     │ │
│  └──────────────┘     └──────────────┘     └─────────────┘ │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

## Features

- **Stealth Browser**: Uses `nodriver` (undetected-chromedriver successor)
- **Hybrid CAPTCHA Solving**: Local OCR (free) → CapSolver API (fallback)
- **Session Extraction**: Converts browser session to API polling
- **Smart Slot Detection**: Scans calendar within date range
- **Sequential Processing**: Handles multiple applicants safely

## Installation

```bash
# Clone/download the project
cd visa_bot

# Install dependencies
pip install -r requirements.txt

# Or manually:
pip install nodriver ddddocr httpx openpyxl aiofiles
```

## Configuration

Edit `config.py` to set:

```python
# Date range for appointments
DATE_RANGE_START = date(2025, 2, 1)
DATE_RANGE_END = date(2025, 3, 31)

# Your CapSolver API key (fallback for CAPTCHA)
CAPSOLVER_API_KEY = "CAP-XXXXXX"

# Polling interval (don't go below 2s)
POLL_INTERVAL = 2.0
```

## Usage

### 1. Create Excel Template

```bash
python main.py --create-template
```

This creates `applicants.xlsx` with columns:
- Country
- Passport Number
- Visa Number
- Primary Mobile
- Primary Email

### 2. Fill Applicant Data

Open `applicants.xlsx` and add your applicants.

### 3. Run the Bot

```bash
# Default settings (dates from config.py)
python main.py

# Custom date range
python main.py --start 2025-02-01 --end 2025-03-31

# Custom Excel file
python main.py --excel my_applicants.xlsx

# Headless mode (no browser UI)
python main.py --headless

# Browser-only mode (no API polling)
python main.py --browser-only

# Full example
python main.py -e applicants.xlsx -s 2025-02-01 -n 2025-03-31 --headless
```

## File Structure

```
visa_bot/
├── config.py           # Settings and selectors
├── captcha_solver.py   # ddddocr + CapSolver hybrid
├── browser_engine.py   # nodriver automation
├── slot_monitor.py     # API polling (Sniper phase)
├── data_handler.py     # Excel processing
├── main.py             # Orchestration
├── requirements.txt    # Dependencies
└── applicants.xlsx     # Input data
```

## Troubleshooting

### CAPTCHA Fails Repeatedly
1. Check CapSolver balance at capsolver.com
2. Ensure API key in config.py is correct
3. Try refreshing CAPTCHA manually in visible mode

### "No Slots Found"
1. The portal genuinely has no availability
2. Expand your date range
3. Check if logged in successfully (look at screenshots)

### Session Extraction Fails
1. Run without `--headless` to observe
2. Check for additional verification steps
3. Use `--browser-only` mode as fallback

### Rate Limited
1. Increase `POLL_INTERVAL` in config.py
2. Use residential proxy (not implemented in base version)

## Extending

### Add Proxy Rotation
```python
# In browser_engine.py start()
self.browser = await uc.start(
    browser_args=[
        f"--proxy-server={proxy_url}",
        ...
    ]
)
```

### Add Telegram Notifications
```python
# After successful booking in main.py
import httpx
async def notify(message):
    await httpx.AsyncClient().post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": message}
    )
```

## Legal Notice

This tool is for educational purposes. Ensure compliance with the target website's Terms of Service. The authors are not responsible for misuse.

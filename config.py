"""
Qatar Visa Bot - Configuration
Environment-aware config that works locally and in production (Docker/Render)
"""

import os
from dataclasses import dataclass, field
from datetime import date
from typing import Optional
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file (local dev)
load_dotenv()


@dataclass
class Config:
    # ============================================
    # Environment Detection
    # ============================================
    # Detect if running in production (Render/Docker)
    IS_PRODUCTION: bool = field(default_factory=lambda: os.getenv("RENDER") == "true" or os.getenv("DOCKER") == "true")
    
    # ============================================
    # Server Settings
    # ============================================
    PORT: int = field(default_factory=lambda: int(os.getenv("PORT", "8000")))
    HOST: str = field(default_factory=lambda: os.getenv("HOST", "0.0.0.0"))
    
    # ============================================
    # Target URLs
    # ============================================
    BASE_URL: str = "https://www.qatarvisacenter.com"
    SCHEDULE_URL: str = "https://www.qatarvisacenter.com/schedule"
    
    # ============================================
    # Date Range for Slot Booking
    # ============================================
    DATE_RANGE_START: date = field(default_factory=lambda: date(2025, 2, 1))
    DATE_RANGE_END: date = field(default_factory=lambda: date(2025, 3, 31))
    
    # ============================================
    # File Paths
    # ============================================
    BASE_DIR: Path = field(default_factory=lambda: Path(__file__).parent)
    EXCEL_PATH: str = "applicants.xlsx"
    LOG_FILE: str = field(default_factory=lambda: str(Path(__file__).parent / "visa_bot.log"))
    SCREENSHOT_DIR: Path = field(default_factory=lambda: Path(__file__).parent / "screenshots")
    DATA_DIR: Path = field(default_factory=lambda: Path(__file__).parent / "data")
    
    # ============================================
    # Browser Settings
    # ============================================
    # Force headless in production, allow override locally
    HEADLESS: bool = field(default_factory=lambda: (
        os.getenv("RENDER") == "true" or 
        os.getenv("DOCKER") == "true" or 
        os.getenv("HEADLESS", "false").lower() == "true"
    ))
    
    # Debug mode (verbose logging)
    DEBUG: bool = field(default_factory=lambda: os.getenv("DEBUG", "false").lower() == "true")
    
    # ============================================
    # CapSolver API (Fallback CAPTCHA Solver)
    # ============================================
    CAPSOLVER_API_KEY: str = field(default_factory=lambda: os.getenv("CAPSOLVER_API_KEY", ""))
    
    # ============================================
    # Proxy Settings (Data Impulse)
    # ============================================
    PROXY_ENABLED: bool = field(default_factory=lambda: os.getenv("PROXY_ENABLED", "false").lower() == "true")
    PROXY_HOST: str = field(default_factory=lambda: os.getenv("PROXY_HOST", "gw.dataimpulse.com"))
    PROXY_PORT: int = field(default_factory=lambda: int(os.getenv("PROXY_PORT", "823")))
    PROXY_USERNAME: str = field(default_factory=lambda: os.getenv("PROXY_USERNAME", ""))
    PROXY_PASSWORD: str = field(default_factory=lambda: os.getenv("PROXY_PASSWORD", ""))
    PROXY_STICKY_MINS: int = field(default_factory=lambda: int(os.getenv("PROXY_STICKY_MINS", "10")))
    PROXY_MAX_ROTATIONS: int = field(default_factory=lambda: int(os.getenv("PROXY_MAX_ROTATIONS", "30")))
    
    # ============================================
    # Session Rotation Settings
    # ============================================
    # How long each browser session runs before rotating (minutes)
    SESSION_DURATION_MINUTES: int = field(default_factory=lambda: int(os.getenv("SESSION_DURATION_MINUTES", "5")))
    
    # Delay between closing and starting new session (seconds)
    SESSION_GAP_SECONDS: int = field(default_factory=lambda: int(os.getenv("SESSION_GAP_SECONDS", "15")))
    
    # ============================================
    # Timing Settings
    # ============================================
    CAPTCHA_MAX_RETRIES: int = 3
    PAGE_LOAD_TIMEOUT: int = 30
    ELEMENT_WAIT_TIMEOUT: int = 10
    POLL_INTERVAL: float = 2.0  # Seconds between slot checks
    
    def __post_init__(self):
        """Create necessary directories after initialization"""
        try:
            self.SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
            self.DATA_DIR.mkdir(parents=True, exist_ok=True)
            
            # Create logs directory if LOG_FILE has a directory component
            log_path = Path(self.LOG_FILE)
            if log_path.parent != Path('.'):
                log_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            print(f"Warning: Could not create directories: {e}")
    
    def get_proxy_url(self) -> Optional[str]:
        """Generate proxy URL for browser if enabled"""
        if not self.PROXY_ENABLED or not self.PROXY_USERNAME:
            return None
        
        return f"http://{self.PROXY_USERNAME}:{self.PROXY_PASSWORD}@{self.PROXY_HOST}:{self.PROXY_PORT}"
    
    def is_headless_required(self) -> bool:
        """Check if headless mode is required (production environment)"""
        return self.IS_PRODUCTION or self.HEADLESS


@dataclass
class Applicant:
    """Applicant data structure"""
    country: str
    passport_number: str
    visa_number: str
    mobile: str
    email: str
    row_index: int = 0  # Track position in Excel


# ============================================
# DOM Selectors for QVC Portal
# ============================================

class Selectors:
    """CSS and XPath selectors for the Qatar Visa Center portal"""
    
    # ============ LANDING PAGE (qatarvisacenter.com) ============
    # Language dropdown
    LANGUAGE_DROPDOWN_TRIGGER = "input[placeholder='-- Select Language --']"
    LANGUAGE_DROPDOWN_ARROW = "div.holder.dropdown-toggle"
    LANGUAGE_DROPDOWN_MENU = "ul.dropdown-menu.show"
    LANGUAGE_OPTION_ENGLISH = "ul.dropdown-menu.show li a"
    
    # Country dropdown (appears after language selection)
    COUNTRY_DROPDOWN_TRIGGER = "input[placeholder='-- Select Country --']"
    COUNTRY_DROPDOWN_ARROW = "div.dropdown input[placeholder='-- Select Country --'] + div.holder"
    COUNTRY_DROPDOWN_MENU = "ul.dropdown-menu"
    COUNTRY_OPTION = "ul.dropdown-menu li a"  # Select by text match
    
    # "Book Appointment" button on landing page
    BOOK_APPOINTMENT_BTN = "body > qvc-root > div.main-container > qvc-home > div.banner-card-menu > div > div > div:nth-child(2) > a"
    
    # Attention Popup
    POPUP_CLOSE_BTN = "#attentionPopup > div > div > div > div.modal-header > button"
    
    # ============ LOGIN PAGE (/schedule) ============
    PASSPORT_INPUT = "input.form-control[placeholder='Passport Number']"
    VISA_INPUT = "input.form-control[placeholder='Visa Number']"
    CAPTCHA_IMAGE = "#captchaImage"
    CAPTCHA_INPUT = "input[name='captchaCode'], input[placeholder*='Captcha']"
    SUBMIT_BTN = "button[type='submit']"
    LOGOUT_BTN = "button[translate='schedule.logout'], .logout-btn, a:has-text('Logout')"
    
    # XPath alternatives (use if CSS fails)
    PASSPORT_INPUT_XPATH = "//input[@placeholder='Passport Number']"
    VISA_INPUT_XPATH = "/html/body/qvc-root/div[2]/qvc-schedule/div/div/div[2]/qvc-appt-type/form/div/div[1]/div[1]/div[1]/div[2]/div/input"
    CAPTCHA_INPUT_XPATH = "/html/body/qvc-root/div[2]/qvc-schedule/div/div/div[2]/qvc-appt-type/form/div/div[1]/div[2]/div[2]/input"
    SUBMIT_BTN_XPATH = "/html/body/qvc-root/div[2]/qvc-schedule/div/div/div[2]/qvc-appt-type/form/div/div[1]/div[3]/div/button"
    
    # Active Session Popup (Login Page)
    SESSION_ACTIVE_CLOSE_BTN = "#invalidOldToken > div > div > div > div.modal-header > button"
    SESSION_ACTIVE_CLOSE_BTN_XPATH = "/html/body/qvc-root/div[2]/qvc-schedule/div/div/div[2]/qvc-appt-type/modal[4]/div/div/div/div[1]/button"
    
    # "OK" Button on Active Session Popup (New Flow)
    SESSION_ACTIVE_OK_BTN = "#invalidOldToken > div > div > div > div.modal-footer > div > modal-footer > button:nth-child(1)"
    SESSION_ACTIVE_OK_BTN_XPATH = "/html/body/qvc-root/div[2]/qvc-schedule/div/div/div[2]/qvc-appt-type/modal[4]/div/div/div/div[3]/div/modal-footer/button[1]"

    # ============ CONTACT DETAILS PAGE ============
    # Primary Contact
    PRIMARY_MOBILE = "#phone"
    PRIMARY_MOBILE_XPATH = "/html/body/qvc-root/div[2]/qvc-schedule/div/div/div[2]/qvc-applicantdetails/div/div[1]/form/div[2]/div[1]/div/input"
    
    PRIMARY_EMAIL = "#email"
    PRIMARY_EMAIL_XPATH = "/html/body/qvc-root/div[2]/qvc-schedule/div/div/div[2]/qvc-applicantdetails/div/div[1]/form/div[2]/div[2]/div/input"
    
    # Applicant Information
    APPLICANT_MOBILE = "#contactNumber"
    APPLICANT_MOBILE_XPATH = "/html/body/qvc-root/div[2]/qvc-schedule/div/div/div[2]/qvc-applicantdetails/div/div[1]/form/div[4]/div[2]/div[2]/div[1]/div[1]/div/input"
    
    APPLICANT_EMAIL = "#emailId"
    APPLICANT_EMAIL_XPATH = "/html/body/qvc-root/div[2]/qvc-schedule/div/div/div[2]/qvc-applicantdetails/div/div[1]/form/div[4]/div[2]/div[2]/div[1]/div[2]/div/input"
    
    # Confirm Details Button (replaces generic Proceed button for this page)
    CONFIRM_DETAILS_BTN = "button[translate='schedule.confirm_applicant']"
    CONFIRM_DETAILS_BTN_XPATH = "/html/body/qvc-root/div[2]/qvc-schedule/div/div/div[2]/qvc-applicantdetails/div/div[1]/div[2]/div/button"
    
    PROCEED_BTN = "button:has-text('Proceed'), button:has-text('Continue'), button:has-text('Next')"
    
    # Notification Popup (appears on applicant details page)
    NOTIFICATION_POPUP_CLOSE_BTN = "#notificationAlert > div > div > div > div.modal-header > button"
    NOTIFICATION_POPUP_CLOSE_BTN_XPATH = "/html/body/qvc-root/div[2]/qvc-schedule/div/div/div[2]/qvc-applicantdetails/modal[2]/div/div/div/div[1]/button"

    # ============ CALENDAR PAGE ============
    CALENDAR_CONTAINER = ".calendar, .datepicker, [class*='calendar']"
    AVAILABLE_DATE = ".available, .open, [class*='available']:not(.disabled)"
    NEXT_MONTH_BTN = ".next, .next-month, [class*='next']"
    TIME_SLOT = ".time-slot, .slot, [class*='time']"
    
    # Slot Page Notification Popup
    SLOT_NOTIFICATION_CLOSE_BTN = "#invalidVisa > div > div > div > div.modal-header > button"
    SLOT_NOTIFICATION_CLOSE_BTN_XPATH = "/html/body/qvc-root/div[2]/qvc-schedule/div/div/div[2]/qvc-slotdetails/modal[5]/div/div/div/div[1]/button"
    
    # QVC Center dropdown
    QVC_CENTER_DROPDOWN = "button[name='selectedVsc']"
    QVC_CENTER_DROPDOWN_XPATH = "//button[@name='selectedVsc']"
    
    CONFIRM_BTN = "button:has-text('Confirm'), button:has-text('Book')"


# ============================================
# Create Global Instances
# ============================================

config = Config()
selectors = Selectors()


# ============================================
# Production Safety Checks
# ============================================

def validate_config():
    """Validate critical config settings for production"""
    errors = []
    
    # Check if proxy is enabled in production without credentials
    if config.IS_PRODUCTION and config.PROXY_ENABLED:
        if not config.PROXY_USERNAME or not config.PROXY_PASSWORD:
            errors.append("Proxy enabled but credentials missing")
    
    # Check if headless is enabled in production
    if config.IS_PRODUCTION and not config.HEADLESS:
        errors.append("Production environment detected but headless mode is disabled")
    
    # Warn about missing CAPTCHA key
    if not config.CAPSOLVER_API_KEY:
        print("⚠️  Warning: CAPSOLVER_API_KEY not set - will use local OCR only")
    
    if errors:
        error_msg = "\n".join(f"  - {e}" for e in errors)
        raise ValueError(f"Configuration errors:\n{error_msg}")


# Run validation on import (only in production)
if config.IS_PRODUCTION:
    try:
        validate_config()
        print(f"✓ Production config validated")
        print(f"  - Headless: {config.HEADLESS}")
        print(f"  - Proxy: {config.PROXY_ENABLED}")
        print(f"  - Port: {config.PORT}")
    except ValueError as e:
        print(f"❌ {e}")
        # Don't exit - let the app handle it

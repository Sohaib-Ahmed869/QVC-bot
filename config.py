"""
Configuration for Qatar Visa Center Appointment Bot
"""
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

@dataclass
class Config:
    # Target URLs
    BASE_URL: str = "https://www.qatarvisacenter.com"
    SCHEDULE_URL: str = "https://www.qatarvisacenter.com/schedule"
    
    # Date range for slot booking (MODIFY THESE)
    DATE_RANGE_START: date = field(default_factory=lambda: date(2025, 2, 1))
    DATE_RANGE_END: date = field(default_factory=lambda: date(2025, 3, 31))
    
    # File paths
    EXCEL_PATH: str = "applicants.xlsx"
    
    # CapSolver API (fallback)
    CAPSOLVER_API_KEY: str = "CAP-978F1FAE90847145128AAA"  
    
    # Timing settings
    CAPTCHA_MAX_RETRIES: int = 3
    PAGE_LOAD_TIMEOUT: int = 30
    ELEMENT_WAIT_TIMEOUT: int = 10
    POLL_INTERVAL: float = 2.0  # Seconds between slot checks
    
  
    HEADLESS: bool = False  # Set True for production
    
    # Proxy settings (Data Impulse)
    PROXY_ENABLED: bool = True
    PROXY_HOST: str = "gw.dataimpulse.com"
    PROXY_PORT: int = 823
    PROXY_USERNAME: str = "ae206a2856858352a07d"
    PROXY_PASSWORD: str = "6ba37afbeb191eb3"
    PROXY_STICKY_MINS: int = 10
    PROXY_MAX_ROTATIONS: int = 30
    
    # Logging
    LOG_FILE: str = "visa_bot.log"
    DEBUG: bool = False


@dataclass
class Applicant:
    country: str
    passport_number: str
    visa_number: str
    mobile: str
    email: str
    row_index: int = 0  # Track position in Excel


# Selectors for the QVC portal (based on DOM inspection)
class Selectors:
    # ============ LANDING PAGE (qatarvisacenter.com) ============
    # Language dropdown
    LANGUAGE_DROPDOWN_TRIGGER = "input[placeholder='-- Select Language --']"
    LANGUAGE_DROPDOWN_ARROW = "div.holder.dropdown-toggle"
    LANGUAGE_DROPDOWN_MENU = "ul.dropdown-menu.show"
    LANGUAGE_OPTION_ENGLISH = "ul.dropdown-menu.show li a"  # First <a> is English
    
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
    SLOT_NOTIFICATION_CLOSE_BTN_XPATH = "/html/body/qvc-root/div[2]/qvc-schedule/div/div/div[2]/qvc-slotdetails/modal[5]/div/div/div/div[1]/button"
    
    # QVC Center dropdown
    QVC_CENTER_DROPDOWN = "button[name='selectedVsc']"
    QVC_CENTER_DROPDOWN_XPATH = "//button[@name='selectedVsc']"
    
    CONFIRM_BTN = "button:has-text('Confirm'), button:has-text('Book')"


config = Config()
selectors = Selectors()
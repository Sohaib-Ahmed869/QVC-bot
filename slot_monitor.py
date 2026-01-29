import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Optional, Tuple, List, Callable
from dataclasses import dataclass
from enum import Enum

import nodriver as uc

logger = logging.getLogger(__name__)


class SlotStatus(Enum):
    SEARCHING = "searching"
    FOUND = "found"
    TIMEOUT = "timeout"
    ERROR = "error"


@dataclass
class CapturedSlot:
    """Result of successful slot capture"""
    date: date
    time: str
    center: str
    captured_at: datetime


class SlotHunter:

    SELECTORS = {
        # Calendar structure
        "calendar": "sb-datepicker",
        "calendar_wrapper": "div.datepicker__wrapper",
        "calendar_table": "div.datepicker__calendar-wrapper",
        
     
        "available_date": "td.datepicker__day:not(.is-disabled)",
        "disabled_date": "td.datepicker__day.is-disabled",
        "date_button": "button.datepicker__button:not([disabled])",
        "all_day_cells": "td.datepicker__day",
        

        "next_month": "button.navigation__button.is-next",
        "prev_month": "button.navigation__button.is-previous",
        
        # Month label - EXACT from inspection
        # <div class="navigation__title-wrapper">contains month/year
        "month_label": "div.navigation__title-wrapper, div.navigation__title",
        "month_span": "div.navigation__title-wrapper span:first-child, div.navigation__title span:first-child",
        "year_span": "div.navigation__title-wrapper span:last-child, div.navigation__title span:last-child",
        
        # Time slots - Based on inspection
        # No slots: <div class="noSlotTimeDiv">No slots available for the selected date</div>
        # When available: container will render with clickable time buttons
        "no_slots_message": "div.noSlotTimeDiv",
        "time_slot_container": "div.time-slots, div.slot-times, div[class*='timeSlot']",
        "available_time": "button.time-slot:not([disabled]), div.time-slot:not(.disabled), button[class*='time']:not([disabled])",
        
        # Appointment type radio (must be selected)
        "appointment_type_normal": "input[name='appointmentType'][value='Normal']",
        "appointment_type_premium": "input[name='appointmentType'][value='Premium']",
        
        # Center dropdown - EXACT from inspection
        "center_dropdown": "button[name='selectedVsc']",
        "center_dropdown_menu": "button[name='selectedVsc'] + ul.dropdown-menu",
        "center_option_xpath": "//button[@name='selectedVsc']/following-sibling::ul//li/a[contains(text(), '{center}')]",
        
        # Confirm/Book button
        "confirm_btn": "button[translate*='book'], button.btn-submit, button[type='submit']:not([disabled])",
        
        # Popups
        "popup_close": ".modal button.close, .modal .btn-close, button.btn-close",
    }
    
    def __init__(
        self,
        page: uc.Tab,
        target_center: str = "Islamabad",
        poll_interval: float = 2.0,
        max_poll_duration: int = 3600,  # 1 hour default
        date_range: Tuple[date, date] = None,
        on_slot_found: Callable = None,
        proxy_manager = None,  # For IP rotation reporting only
    ):

        self.page = page
        self.target_center = target_center
        self.poll_interval = poll_interval
        self.max_poll_duration = max_poll_duration
        self.date_range = date_range
        self.on_slot_found = on_slot_found
        self.proxy_manager = proxy_manager
        
        # State
        self.status = SlotStatus.SEARCHING
        self.poll_count = 0
        self.current_month_index = 0
        self.max_months = 5  # Jan → May based on your observation
        self._stop_flag = False
        self._consecutive_empty_polls = 0  # Track empty results for rotation trigger
        
    async def _select_center(self) -> bool:
        """Select the target QVC center from dropdown"""
        try:
            logger.info(f"Selecting center: {self.target_center}")
            
            # Check if already selected using JS
            current_result = await self.page.evaluate("""
                (() => {
                    const btn = document.querySelector('button[name="selectedVsc"]');
                    return btn ? btn.textContent.trim() : '';
                })()
            """)
            
            # Handle nodriver return
            current_selection = current_result
            if isinstance(current_result, dict) and 'value' in current_result:
                current_selection = current_result['value']
            
            if current_selection and self.target_center.lower() in str(current_selection).lower():
                logger.info(f"Center '{self.target_center}' already selected")
                return True
            
            # Click dropdown trigger to open menu
            dropdown = await self.page.select(self.SELECTORS["center_dropdown"], timeout=5)
            if not dropdown:
                logger.warning("Center dropdown not found")
                return False
                
            await dropdown.click()
            await asyncio.sleep(0.5)
            
            # Find and click the center option
            # Structure: <ul class="dropdown-menu"><li><a>Islamabad</a></li>...</ul>
            xpath = f"//button[@name='selectedVsc']/following-sibling::ul//li/a[contains(text(), '{self.target_center}')]"
            option = await self.page.find(xpath, timeout=3)
            
            if option:
                await option.click()
                logger.info(f"Selected center: {self.target_center}")
                await asyncio.sleep(1)  # Wait for calendar to reload
                return True
            
            # Fallback: Try CSS selector
            css_option = f"button[name='selectedVsc'] + ul.dropdown-menu li a"
            options = await self.page.select_all(css_option)
            for opt in options:
                text = opt.text or ""
                if self.target_center.lower() in text.lower():
                    await opt.click()
                    logger.info(f"Selected center: {self.target_center} (fallback)")
                    await asyncio.sleep(1)
                    return True
            
            logger.error(f"Center option '{self.target_center}' not found")
            return False
            
        except Exception as e:
            logger.error(f"Failed to select center: {e}")
            return False
    
    async def _get_current_month_year(self) -> Tuple[int, int]:
        """Get currently displayed month and year from calendar header"""
        try:
            # Use page.evaluate to get month/year text directly via JS
            result = await self.page.evaluate("""
                (() => {
                    const titleWrapper = document.querySelector('div.navigation__title-wrapper') || 
                                         document.querySelector('div.navigation__title');
                    if (titleWrapper) {
                        const spans = titleWrapper.querySelectorAll('span');
                        if (spans.length >= 2) {
                            return {
                                month: spans[0].textContent.trim(),
                                year: spans[spans.length - 1].textContent.trim()
                            };
                        }
                        // Fallback: get full text
                        return { text: titleWrapper.textContent.trim() };
                    }
                    return null;
                })()
            """)
            
            # Convert nodriver's list format to dict if needed
            if isinstance(result, list):
                result_dict = {}
                for item in result:
                    if isinstance(item, list) and len(item) == 2:
                        key = item[0]
                        val_obj = item[1]
                        if isinstance(val_obj, dict) and 'value' in val_obj:
                            result_dict[key] = val_obj['value']
                        else:
                            result_dict[key] = val_obj
                result = result_dict
            
            months_map = {
                "january": 1, "jan": 1,
                "february": 2, "feb": 2,
                "march": 3, "mar": 3,
                "april": 4, "apr": 4,
                "may": 5,
                "june": 6, "jun": 6,
                "july": 7, "jul": 7,
                "august": 8, "aug": 8,
                "september": 9, "sep": 9,
                "october": 10, "oct": 10,
                "november": 11, "nov": 11,
                "december": 12, "dec": 12,
            }
            
            if result:
                if 'month' in result and 'year' in result:
                    month = months_map.get(str(result['month']).lower(), 1)
                    year = int(result['year'])
                    logger.debug(f"Current calendar: {result['month']} {result['year']} -> {month}/{year}")
                    return (month, year)
                elif 'text' in result:
                    # Parse "January 2026" format
                    text = str(result['text']).replace('\xa0', ' ').strip()
                    parts = text.split()
                    if len(parts) >= 2:
                        month = months_map.get(parts[0].lower(), 1)
                        year = int(parts[-1])
                        return (month, year)
                    
        except Exception as e:
            logger.debug(f"Could not parse month label: {e}")
        
        # Fallback to current date
        now = datetime.now()
        return (now.month, now.year)
    
    async def _go_to_next_month(self) -> bool:
        """Click next month button. Returns False if button is disabled/unavailable."""
        try:
            next_btn = await self.page.select(self.SELECTORS["next_month"], timeout=2)
            if not next_btn:
                logger.debug("Next month button not found")
                return False
            
            # Check if button is disabled using nodriver's approach
            # nodriver elements have .attrs dict for attributes
            is_disabled = next_btn.attrs.get('disabled') is not None or 'disabled' in str(next_btn.attrs)
            
            if is_disabled:
                logger.debug("Next month button is disabled (reached max month)")
                return False
            
            await next_btn.click()
            await asyncio.sleep(0.5)  # Wait for calendar animation
            self.current_month_index += 1
            
            month, year = await self._get_current_month_year()
            logger.debug(f"Navigated to: {month}/{year}")
            return True
            
        except Exception as e:
            logger.debug(f"Failed to navigate to next month: {e}")
            return False
    
    async def _go_to_first_month(self) -> bool:
        """Reset calendar to first available month"""
        try:
            # Click prev until we can't anymore
            clicks = 0
            for _ in range(self.max_months):
                prev_btn = await self.page.select(self.SELECTORS["prev_month"], timeout=1)
                if not prev_btn:
                    break
                
                # Check if button is disabled
                is_disabled = prev_btn.attrs.get('disabled') is not None or 'disabled' in str(prev_btn.attrs)
                
                if is_disabled:
                    break
                
                await prev_btn.click()
                await asyncio.sleep(0.3)
                clicks += 1
            
            self.current_month_index = 0
            if clicks > 0:
                logger.debug(f"Reset calendar: clicked prev {clicks} times")
            return True
            
        except Exception as e:
            logger.debug(f"Failed to reset to first month: {e}")
            return False
    
    async def _has_any_available_date_in_month(self) -> bool:
        """Quick check if current month has ANY available dates (fast pre-scan)"""
        try:
            result = await self.page.evaluate("""
                (() => {
                    const cells = document.querySelectorAll('td.datepicker__day');
                    for (const cell of cells) {
                        if (!cell.classList.contains('is-disabled')) {
                            const btn = cell.querySelector('button');
                            if (btn && !btn.disabled) {
                                return true;
                            }
                        }
                    }
                    return false;
                })()
            """)
            # Handle various return formats
            if isinstance(result, bool):
                return result
            if isinstance(result, dict) and 'value' in result:
                return result['value']
            return bool(result)
        except:
            return False
    
    async def _get_available_date_count(self) -> int:
        """Get count of available dates in current month view"""
        try:
            result = await self.page.evaluate("""
                (() => {
                    let count = 0;
                    const cells = document.querySelectorAll('td.datepicker__day:not(.is-disabled)');
                    cells.forEach(cell => {
                        const btn = cell.querySelector('button:not([disabled])');
                        if (btn) count++;
                    });
                    return count;
                })()
            """)
            # nodriver may return int directly or wrapped
            if isinstance(result, int):
                return result
            if isinstance(result, dict) and 'value' in result:
                return result['value']
            return int(result) if result else 0
        except:
            return 0

    async def _scan_current_month(self) -> List[uc.Element]:

        try:
            month, year = await self._get_current_month_year()
            
            # Get ALL day cells first
            all_cells = await self.page.select_all(self.SELECTORS["all_day_cells"])
            
            if not all_cells:
                logger.debug(f"No date cells found in {month}/{year}")
                return []
            
            # Filter to only available (non-disabled) cells
            available = []
            for cell in all_cells:
                try:
                    # Check if cell has is-disabled class using attrs
                    cell_class = cell.attrs.get('class', '') or cell.attrs.get('class_', '')
                    if 'is-disabled' in cell_class:
                        continue
                    
                    # This cell might be available - add it
                    available.append(cell)
                    
                except Exception as e:
                    logger.debug(f"Error checking cell: {e}")
                    continue
            
            if available:
                logger.info(f"✓ Found {len(available)} available date(s) in {month}/{year}!")
            else:
                logger.debug(f"No available dates in {month}/{year}")
            
            return available
            
        except Exception as e:
            logger.debug(f"Error scanning month: {e}")
            return []
    
    async def _is_date_in_range(self, date_element: uc.Element) -> bool:
        """Check if a date element falls within our target range"""
        if not self.date_range:
            return True  # No range restriction
        
        try:
            # Get day number from element text using nodriver's text property
            day_text = date_element.text or ""
            if not day_text:
                # Fallback: try to get inner text via node
                day_text = str(date_element.text_all) if hasattr(date_element, 'text_all') else ""
            
            # Extract just the number
            day = int(''.join(filter(str.isdigit, day_text.strip())))
            
            month, year = await self._get_current_month_year()
            element_date = date(year, month, day)
            
            start, end = self.date_range
            return start <= element_date <= end
            
        except Exception as e:
            logger.debug(f"Could not determine date for element: {e}")
            return True  # Accept if we can't verify
    
    async def _click_date(self, day_number: int) -> bool:
        """Click on an available date by day number, re-querying DOM to avoid stale elements"""
        try:
            # Re-query DOM to get fresh element reference
            selector = f"td.datepicker__day:not(.is-disabled) button"
            elements = await self.page.select_all(selector, timeout=5)
            
            if not elements:
                logger.warning("No available date elements found")
                return False
            
            # Find the element with matching day number
            for el in elements:
                try:
                    day_text = el.text or ""
                    el_day = int(''.join(filter(str.isdigit, day_text.strip())) or "0")
                    if el_day == day_number:
                        await el.click()
                        logger.info(f"Clicked on date: {day_number}")
                        await asyncio.sleep(1.5)  # Wait for time slots to load
                        return True
                except (ValueError, TypeError):
                    continue
            
            # Fallback: Try clicking by index if exact match not found
            logger.warning(f"Day {day_number} not found, trying first available")
            if elements:
                await elements[0].click()
                await asyncio.sleep(1.5)
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Failed to click date {day_number}: {e}")
            return False
    
    async def _select_time_slot(self) -> Optional[str]:

        try:
            # Wait for time slots to load after date click
            await asyncio.sleep(1.5)
            
            # Check if "No slots" message is showing using JS
            no_slots_result = await self.page.evaluate("""
                (() => {
                    const noSlot = document.querySelector('div.noSlotTimeDiv');
                    return noSlot ? noSlot.textContent.trim() : null;
                })()
            """)
            
            # Handle nodriver return format
            no_slots_text = no_slots_result
            if isinstance(no_slots_result, dict) and 'value' in no_slots_result:
                no_slots_text = no_slots_result['value']
            
            if no_slots_text and "no slot" in str(no_slots_text).lower():
                logger.warning(f"No time slots available: {no_slots_text}")
                return None
            
            # First, select appointment type (Normal) if not already selected
            try:
                normal_radio = await self.page.select(self.SELECTORS["appointment_type_normal"], timeout=2)
                if normal_radio:
                    await normal_radio.click()
                    logger.debug("Selected 'Normal' appointment type")
                    await asyncio.sleep(0.5)
            except:
                pass  # Radio might already be selected or not present
            
            # Try to find and click time slot via JS (most reliable)
            time_result = await self.page.evaluate("""
                () => {
                    // Look for any button/element that looks like a time slot
                    const selectors = [
                        'button.time-slot:not([disabled])',
                        'div.time-slot:not(.disabled):not(.booked)',
                        'button[class*="time"]:not([disabled])',
                        '.slot-item:not(.disabled) button',
                        'a.time-slot:not(.disabled)'
                    ];
                    
                    for (const selector of selectors) {
                        const elements = document.querySelectorAll(selector);
                        for (const el of elements) {
                            const text = el.textContent.trim();
                            // Check if it looks like a time (HH:MM or has AM/PM)
                            if (/\\d{1,2}[:\\s]?\\d{0,2}\\s*(AM|PM)?/i.test(text) || 
                                text.includes(':') || 
                                /^\\d{1,2}\\s*(AM|PM)$/i.test(text)) {
                                el.click();
                                return { success: true, text: text };
                            }
                        }
                    }
                    
                    // Fallback: Look for ANY clickable time-like element
                    const allButtons = document.querySelectorAll('button:not([disabled])');
                    for (const btn of allButtons) {
                        const text = btn.textContent.trim();
                        if (/\\d{1,2}:\\d{2}/.test(text)) {
                            btn.click();
                            return { success: true, text: text };
                        }
                    }
                    
                    return { success: false, text: null };
                }
            """)
            
            if time_result and time_result.get('success'):
                slot_text = time_result.get('text', 'Unknown')
                logger.info(f"[OK] Selected time slot: {slot_text}")
                await asyncio.sleep(0.5)
                return slot_text
            
            # Handle nodriver list format
            if isinstance(time_result, list):
                result_dict = {}
                for item in time_result:
                    if isinstance(item, list) and len(item) == 2:
                        key = item[0]
                        val_obj = item[1]
                        if isinstance(val_obj, dict) and 'value' in val_obj:
                            result_dict[key] = val_obj['value']
                        else:
                            result_dict[key] = val_obj
                if result_dict.get('success'):
                    slot_text = result_dict.get('text', 'Unknown')
                    logger.info(f"[OK] Selected time slot: {slot_text}")
                    await asyncio.sleep(0.5)
                    return slot_text
            
            logger.warning("No time slots found after date selection")
            return None
            
        except Exception as e:
            logger.error(f"Failed to select time slot: {e}")
            return None
    
    async def _close_any_popup(self) -> bool:
        """Close any notification/error popup that might appear"""
        try:
            close_btn = await self.page.select(self.SELECTORS["popup_close"], timeout=1)
            if close_btn:
                await close_btn.click()
                await asyncio.sleep(0.5)
                return True
        except:
            pass
        return False
    
    async def _refresh_calendar(self):
        """
        Refresh calendar data without full page reload.
        Toggle center selection or navigate months to trigger refresh.
        """
        try:
            # Method 1: Go to first month and back (forces re-fetch)
            await self._go_to_first_month()
            await asyncio.sleep(0.3)
            
        except Exception as e:
            logger.debug(f"Calendar refresh failed: {e}")
    
    async def hunt(self) -> Optional[CapturedSlot]:
        """
        Main hunting loop. Continuously polls calendar until slot found or timeout.
        
        Returns:
            CapturedSlot if successful, None if timeout/error
        """
        logger.info("=" * 50)
        logger.info("SLOT HUNTER STARTED")
        logger.info(f"Center: {self.target_center}")
        logger.info(f"Poll interval: {self.poll_interval}s")
        logger.info(f"Max duration: {self.max_poll_duration}s ({self.max_poll_duration/60:.0f} min)")
        if self.date_range:
            logger.info(f"Date range: {self.date_range[0]} to {self.date_range[1]}")
        logger.info("=" * 50)
        
        start_time = asyncio.get_event_loop().time()
        
        # Initial setup
        await self._close_any_popup()
        await self._select_center()
        await asyncio.sleep(1)
        
        # Select Normal appointment type once at start
        try:
            normal_radio = await self.page.select(self.SELECTORS["appointment_type_normal"], timeout=2)
            if normal_radio:
                await normal_radio.click()
                logger.info("Selected 'Normal' appointment type")
        except:
            pass
        
        while not self._stop_flag:
            elapsed = asyncio.get_event_loop().time() - start_time
            
            # Check timeout
            if elapsed > self.max_poll_duration:
                logger.warning(f"Slot hunting timeout after {elapsed:.0f}s ({elapsed/60:.1f} min)")
                self.status = SlotStatus.TIMEOUT
                return None
            
            self.poll_count += 1
            remaining = self.max_poll_duration - elapsed
            
            # Log every 10th poll or first 5
            if self.poll_count <= 5 or self.poll_count % 10 == 0:
                logger.info(f"[Poll #{self.poll_count}] Scanning all months... ({remaining:.0f}s / {remaining/60:.1f}min remaining)")
            
            try:
                # Reset to first month
                await self._go_to_first_month()
                await asyncio.sleep(0.3)
                
                # Scan through all available months
                months_scanned = 0
                for month_idx in range(self.max_months):
                    month, year = await self._get_current_month_year()
                    
                    # FAST CHECK: Any available dates in this month?
                    available_count = await self._get_available_date_count()
                    
                    if available_count > 0:
                        # Reset failure counter on success
                        self._consecutive_empty_polls = 0
                        if self.proxy_manager:
                            await self.proxy_manager.report_success()
                            
                        # SLOTS FOUND - Detection mode: just log and return
                        logger.info("!" * 50)
                        logger.info(f"🎉 AVAILABLE DATES DETECTED: {available_count} in {month}/{year}!")
                        logger.info("!" * 50)
                        
                        # Get day numbers for logging (without clicking)
                        available_dates = await self._scan_current_month()
                        found_dates = []
                        for date_el in available_dates:
                            try:
                                day_text = date_el.text or ""
                                day_num = int(''.join(filter(str.isdigit, day_text.strip())) or "0")
                                if day_num > 0:
                                    if await self._is_date_in_range(date_el):
                                        found_dates.append(day_num)
                            except (ValueError, TypeError):
                                continue
                        
                        # Build result with first available date (no time slot - detection only)
                        try:
                            first_day = found_dates[0] if found_dates else 1
                            slot_date = date(year, month, first_day)
                        except:
                            slot_date = date.today()
                        
                        result = CapturedSlot(
                            date=slot_date,
                            time="DETECTED (not clicked)",  # Detection mode - no time slot selected
                            center=self.target_center,
                            captured_at=datetime.now()
                        )
                        
                        self.status = SlotStatus.FOUND
                        logger.info("=" * 50)
                        logger.info(f"🎯 SLOT DETECTED: {result.date}")
                        logger.info(f"   Available dates in {month}/{year}: {found_dates}")
                        logger.info(f"   Center: {result.center}")
                        logger.info(f"   Detected at: {result.captured_at}")
                        logger.info(f"   Total polls: {self.poll_count}")
                        logger.info(f"   Time elapsed: {elapsed:.0f}s")
                        logger.info("=" * 50)
                        logger.info("✓ Detection complete - NOT clicking/booking (detection mode)")
                        
                        if self.on_slot_found:
                            await self.on_slot_found(result)
                        
                        return result
                        
                    else:
                        # No slots in this month
                        pass

                # If we scanned all months and found nothing
                if months_scanned >= self.max_months and available_count == 0:
                    self._consecutive_empty_polls += 1
                    
                    # If we've seen empty results for too long, might be IP-specific blocking
                    # Rotate every ~50 empty polls (100 seconds at 2s interval)
                    if self._consecutive_empty_polls >= 50 and self.proxy_manager:
                        logger.warning("Extended empty results - raising soft block error")
                        raise Exception("Soft blocked suspected")
            
                # Update months scanned counter
                months_scanned += 1
                
                # Go to next month
                if not await self._go_to_next_month():
                    break
                await asyncio.sleep(1)
            
                # If we get here, no slots found in any month this poll
                if self.poll_count <= 5 or self.poll_count % 30 == 0:
                    logger.debug(f"No slots in {months_scanned} months. Waiting {self.poll_interval}s...")
            
                await asyncio.sleep(self.poll_interval)
            
            except Exception as e:
                logger.error(f"Error during poll #{self.poll_count}: {e}")
                
                # Propagate specific blocking errors to browser_engine
                if "blocked" in str(e).lower():
                    raise e
                        
                await asyncio.sleep(self.poll_interval)
        
        self.status = SlotStatus.ERROR
        return None
    
    def stop(self):
        """Stop the hunting loop"""
        logger.info("Stop signal received")
        self._stop_flag = True
    
    async def hunt_with_callback(
        self,
        on_poll: Callable = None,
        on_found: Callable = None,
        on_timeout: Callable = None,
    ) -> Optional[CapturedSlot]:
        """
        Hunt with lifecycle callbacks for integration with main bot.
        
        Args:
            on_poll: Called each poll cycle with (poll_count, elapsed_time)
            on_found: Called when slot found with (CapturedSlot)
            on_timeout: Called on timeout
        """
        self.on_slot_found = on_found
        
        # Wrap hunt with callbacks
        start_time = asyncio.get_event_loop().time()
        
        result = await self.hunt()
        
        if result and on_found:
            await on_found(result)
        elif self.status == SlotStatus.TIMEOUT and on_timeout:
            await on_timeout()
        
        return result
    
import asyncio
import base64
import json
import os
import uuid
import shutil
import tempfile
from datetime import date, datetime
from typing import Optional, Tuple
import logging
import nodriver as uc
from   nodriver import cdp
from config import config, selectors, Applicant
from captcha_solver import CaptchaSolver
from bandwidth_monitor import bandwidth_monitor
from proxy_manager import ProxyManager, ProxyConfig

logger = logging.getLogger(__name__)

import platform
import shutil as _shutil

def _find_chrome() -> Optional[str]:

    env_path = os.environ.get('CHROME_PATH') or os.environ.get('CHROMIUM_PATH')
    if env_path and os.path.isfile(env_path):
        logger.info(f"Chrome from env: {env_path}")
        return env_path

    for name in ('google-chrome', 'google-chrome-stable', 'chromium-browser',
                 'chromium', 'chrome', 'chrome.exe'):
        found = _shutil.which(name)
        if found:
            logger.info(f"Chrome from PATH: {found}")
            return found

    system = platform.system()
    candidates: list[str] = []

    if system == 'Windows':
        candidates = [
            os.path.expandvars(r'%ProgramFiles%\Google\Chrome\Application\chrome.exe'),
            os.path.expandvars(r'%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe'),
            os.path.expandvars(r'%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe'),
        ]
    elif system == 'Darwin':
        candidates = [
            '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
            '/Applications/Chromium.app/Contents/MacOS/Chromium',
        ]
    else:
        candidates = [
            '/usr/bin/google-chrome',
            '/usr/bin/google-chrome-stable',
            '/usr/bin/chromium-browser',
            '/usr/bin/chromium',
            '/snap/bin/chromium',
            '/opt/google/chrome/chrome',
            '/opt/google/chrome/google-chrome',
            '/opt/chromium/chrome',
            '/usr/lib/chromium/chromium',
        ]

    for p in candidates:
        if os.path.isfile(p):
            logger.info(f"Chrome at known path: {p}")
            return p

    logger.warning("Chrome/Chromium not found — letting nodriver auto-detect")
    return None


def _temp_user_data_dir() -> str:
    base = tempfile.gettempdir()
    return os.path.join(base, f'chrome_uc_{uuid.uuid4().hex[:8]}')


DEBUG_HTML_DIR = os.path.join(os.path.dirname(__file__), "debug_html")
os.makedirs(DEBUG_HTML_DIR, exist_ok=True)


class BrowserEngine:
    def __init__(self, proxy_manager: ProxyManager = None):
        self.browser: Optional[uc.Browser] = None
        self.page: Optional[uc.Tab] = None
        self.solver = CaptchaSolver(config.CAPSOLVER_API_KEY)
        self.proxy_manager = proxy_manager
        self._current_proxy: Optional[ProxyConfig] = None
        self._proxy_ext_dir: Optional[str] = None
        self._session_id = datetime.now().strftime("%Y%m%d_%H%M%S")

    async def _log_html_snapshot(self, event_name: str, selector: str = None):
        if not self.page:
            return
        try:
            timestamp = datetime.now().strftime("%H%M%S")
            filename = f"{self._session_id}_{timestamp}_{event_name}.html"
            filepath = os.path.join(DEBUG_HTML_DIR, filename)

            if selector:
                html = await self.page.evaluate(f'''
                    (() => {{
                        const el = document.querySelector("{selector}");
                        return el ? el.outerHTML : "ELEMENT NOT FOUND: {selector}";
                    }})()
                ''')
            else:
                html = await self.page.evaluate("document.documentElement.outerHTML")

            with open(filepath, "w", encoding="utf-8") as f:
                f.write(f"<!-- Event: {event_name} -->\n")
                f.write(f"<!-- URL: {self.page.url} -->\n")
                f.write(f"<!-- Time: {datetime.now().isoformat()} -->\n")
                if selector:
                    f.write(f"<!-- Selector: {selector} -->\n")
                f.write(html)

            html_preview = html[:200].replace('\n', ' ').replace('\r', '')
            logger.info(f"[HTML] {event_name}: {filepath}")
            logger.debug(f"[HTML Preview] {html_preview}...")

        except Exception as e:
            logger.warning(f"Failed to capture HTML snapshot: {e}")

    def _create_proxy_auth_extension(self) -> Optional[str]:
        if not self._current_proxy:
            return None
        try:
            manifest = {
                "version": "1.0.0",
                "manifest_version": 3,
                "name": "Proxy Auth Helper",
                "permissions": ["proxy", "webRequest", "webRequestAuthProvider"],
                "host_permissions": ["<all_urls>"],
                "background": {"service_worker": "background.js"}
            }
            background_js = f"""
chrome.webRequest.onAuthRequired.addListener(
    function(details, callbackFn) {{
        callbackFn({{
            authCredentials: {{
                username: "{self._current_proxy.session_username}",
                password: "{self._current_proxy.password}"
            }}
        }});
    }},
    {{urls: ["<all_urls>"]}},
    ["asyncBlocking"]
);
"""
            ext_dir = tempfile.mkdtemp(prefix="qvc_proxy_auth_")
            with open(os.path.join(ext_dir, "manifest.json"), "w") as f:
                json.dump(manifest, f, indent=2)
            with open(os.path.join(ext_dir, "background.js"), "w") as f:
                f.write(background_js)
            logger.info(f"Created proxy auth extension at: {ext_dir}")
            return ext_dir
        except Exception as e:
            logger.error(f"Failed to create proxy auth extension: {e}")
            return None

    async def _setup_cdp_proxy_auth(self):
        try:
            tab = self.browser.main_tab
            if not tab:
                logger.warning("No main tab available for CDP proxy auth setup")
                return

            username = self._current_proxy.session_username
            password = self._current_proxy.password

            await tab.send(cdp.fetch.enable(handle_auth_requests=True))

            def _handle_request_paused(event: cdp.fetch.RequestPaused, connection):
                try:
                    asyncio.ensure_future(
                        connection.send(cdp.fetch.continue_request(request_id=event.request_id))
                    )
                except Exception as e:
                    logger.warning(f"Request continue error: {e}")

            tab.add_handler(cdp.fetch.RequestPaused, _handle_request_paused)

            def _handle_proxy_auth(event: cdp.fetch.AuthRequired, connection):
                try:
                    asyncio.ensure_future(
                        connection.send(cdp.fetch.continue_with_auth(
                            request_id=event.request_id,
                            auth_challenge_response=cdp.fetch.AuthChallengeResponse(
                                response="ProvideCredentials",
                                username=username,
                                password=password
                            )
                        ))
                    )
                except Exception as e:
                    logger.warning(f"Proxy auth handler error: {e}")

            tab.add_handler(cdp.fetch.AuthRequired, _handle_proxy_auth)
            logger.info(f"CDP proxy auth handler configured (user: {username})")

        except Exception as e:
            logger.warning(f"CDP proxy auth setup failed: {e} — falling back to extension")
            if not self._proxy_ext_dir:
                self._proxy_ext_dir = self._create_proxy_auth_extension()

    async def start(self):
        logger.info("Starting browser...")

        # Minimal flags — won't break Angular JS rendering
        browser_args = [
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--window-size=1920,1080",
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
            "--mute-audio",
            "--disable-gpu",
        ]

        # Linux/Docker only — do NOT add --single-process, it breaks Angular
        if platform.system() != "Windows":
            browser_args += [
                "--no-zygote",
            ]

        is_docker = os.path.exists('/.dockerenv') or os.environ.get('DOCKER', '') == '1'
        headless = config.HEADLESS
        if is_docker and not headless:
            logger.info("Docker detected — using Xvfb virtual display (headed mode)")

        if self.proxy_manager:
            self._current_proxy = self.proxy_manager.current
            proxy_server = f"http://{self._current_proxy.host}:{self._current_proxy.port}"
            browser_args.append(f"--proxy-server={proxy_server}")
            logger.info(f"Proxy configured: {self._current_proxy.host}:{self._current_proxy.port}")
            logger.info(f"Session ID: {self._current_proxy.session_id}")

        try:
            _chrome_path = _find_chrome()
            _user_data_dir = _temp_user_data_dir()

            browser_config = uc.Config(
                headless=headless,
                browser_executable_path=_chrome_path,
                browser_args=browser_args,
                sandbox=False,
                user_data_dir=_user_data_dir
            )

            max_start_retries = 3
            for attempt in range(max_start_retries):
                try:
                    self.browser = await uc.start(config=browser_config)
                    logger.info(f"Browser process started (attempt {attempt + 1})")
                    break
                except Exception as e:
                    logger.warning(f"Browser start attempt {attempt + 1} failed: {e}")
                    if attempt < max_start_retries - 1:
                        browser_config = uc.Config(
                            headless=headless,
                            browser_executable_path=_chrome_path,
                            browser_args=browser_args,
                            sandbox=False,
                            user_data_dir=_temp_user_data_dir()
                        )
                        await asyncio.sleep(2)
                    else:
                        raise

            if self.proxy_manager and self._current_proxy:
                await self._setup_cdp_proxy_auth()

            logger.info(f"Navigating to {config.BASE_URL}...")
            max_nav_retries = 3

            for attempt in range(max_nav_retries):
                try:
                    self.page = await asyncio.wait_for(
                        self.browser.get(config.BASE_URL, new_tab=False),
                        timeout=30
                    )

                    # Give Angular more time to boot
                    await asyncio.sleep(8)

                    try:
                        title = await self.page.evaluate("document.title")
                        url = self.page.url
                        body_text = await self.page.evaluate(
                            "document.body ? document.body.innerText.substring(0, 300) : 'NO BODY'"
                        )
                        logger.info(f"Page loaded - URL: {url}")
                        logger.info(f"Page title: '{title}'")
                        logger.info(f"Page body preview: {body_text[:200]}")

                        if title and "ERR_" in str(title).upper():
                            raise ConnectionError(f"Page load failed - error title: {title}")

                        if "qatarvisacenter" not in str(url).lower() and attempt < max_nav_retries - 1:
                            logger.warning(f"Unexpected URL: {url}, retrying...")
                            await asyncio.sleep(2)
                            continue

                        if not title:
                            logger.info("Title is empty (JS may still be loading) — URL is valid, continuing")

                        break

                    except Exception as e:
                        logger.warning(f"Page verification failed (attempt {attempt + 1}): {e}")
                        if attempt < max_nav_retries - 1:
                            await asyncio.sleep(2)
                        else:
                            raise

                except Exception as e:
                    logger.warning(f"Navigation attempt {attempt + 1} failed: {e}")
                    if attempt < max_nav_retries - 1:
                        await asyncio.sleep(2)
                    else:
                        raise ConnectionError(f"Failed to load {config.BASE_URL} after {max_nav_retries} attempts")

            if self.proxy_manager:
                try:
                    ip = await self.proxy_manager.verify_ip()
                    if ip:
                        logger.info(f"✓ Proxy IP verified: {ip}")
                    else:
                        logger.warning("Could not verify proxy IP - continuing anyway")
                except Exception as e:
                    logger.warning(f"Proxy verification failed: {e}")

            await bandwidth_monitor.attach_to_page(self.page)

            title = await self.page.evaluate("document.title")
            url = self.page.url
            logger.info(f"Navigated to: {url} | Title: {title}")

            if title and ("ERR_" in str(title) or "available" in str(title).lower()):
                logger.error(f"Navigation failed - Page title: {title}")
                if self.proxy_manager:
                    await self.proxy_manager.report_failure("connection")
                raise ConnectionError(f"Failed to load {config.BASE_URL}")

            logger.info("Browser started successfully")

        except Exception as e:
            logger.error(f"Browser start failed: {e}")
            if self.proxy_manager:
                await self.proxy_manager.report_failure("connection")
            await self.close()
            raise

    async def close(self):
        if self.browser:
            try:
                self.browser.stop()
                logger.info("Browser closed")
            except Exception as e:
                logger.warning(f"Error closing browser: {e}")
            self.browser = None
            self.page = None

        if self._proxy_ext_dir:
            try:
                shutil.rmtree(self._proxy_ext_dir)
            except Exception as e:
                logger.warning(f"Failed to cleanup proxy extension: {e}")
            self._proxy_ext_dir = None

    async def restart_with_new_ip(self) -> bool:
        if not self.proxy_manager:
            logger.warning("No proxy manager configured - cannot rotate IP")
            return False

        logger.info("Restarting browser with new IP...")
        await self.close()
        await asyncio.sleep(2)
        await self.proxy_manager.rotate(reason="manual_restart")

        try:
            await self.start()
            logger.info("✓ Browser restarted with new IP successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to restart browser: {e}")
            return False

    async def _handle_request_error(self, error: Exception) -> bool:
        if not self.proxy_manager:
            return False

        error_str = str(error).lower()

        if any(x in error_str for x in ['429', 'rate limit', 'too many requests']):
            logger.warning("Rate limit detected - rotating IP")
            rotated = await self.proxy_manager.report_failure("rate_limit")
            if rotated:
                await self.restart_with_new_ip()
                return True

        if any(x in error_str for x in ['connection refused', 'timeout', 'unreachable', 'reset by peer', 'failed to load']):
            logger.warning("Connection error detected - rotating IP")
            rotated = await self.proxy_manager.report_failure("connection")
            if rotated:
                await self.restart_with_new_ip()
                return True

        if any(x in error_str for x in ['blocked', 'forbidden', '403', 'access denied']):
            logger.warning("Block detected - rotating IP")
            rotated = await self.proxy_manager.report_failure("blocked")
            if rotated:
                await self.restart_with_new_ip()
                return True

        if any(x in error_str for x in ['407', 'proxy auth', 'authentication required']):
            logger.error("Proxy authentication failed - check credentials")
            rotated = await self.proxy_manager.report_failure("connection")
            if rotated:
                await self.restart_with_new_ip()
                return True

        return False

    async def _wait_for(self, selector: str, timeout: int = None) -> Optional[uc.Element]:
        timeout = timeout or config.ELEMENT_WAIT_TIMEOUT
        try:
            element = await self.page.select(selector, timeout=timeout)
            return element
        except Exception as e:
            logger.debug(f"Element not found: {selector} - {e}")
            return None

    async def _click(self, selector: str) -> bool:
        element = await self._wait_for(selector)
        if element:
            await element.click()
            return True
        return False

    async def _type(self, selector: str, text: str, clear: bool = True) -> bool:
        try:
            element = await self._wait_for(selector)
            if element:
                await element.click()
                await asyncio.sleep(0.2)
                if clear:
                    await element.clear_input()
                await element.send_keys(text)
                return True
            return False
        except Exception as e:
            logger.debug(f"Type failed for {selector}: {e}")
            return False

    async def _type_xpath(self, xpath: str, text: str, clear: bool = True) -> bool:
        try:
            element = await self.page.find(xpath, timeout=config.ELEMENT_WAIT_TIMEOUT)
            if element:
                await element.click()
                await asyncio.sleep(0.2)
                if clear:
                    await element.clear_input()
                await element.send_keys(text)
                return True
            return False
        except Exception as e:
            logger.debug(f"XPath type failed: {e}")
            return False

    # ========================================
    # Navigation
    # ========================================

    async def navigate_landing_page(self, country: str, language: str = "English") -> bool:
        logger.info(f"Navigating landing page - Language: {language}, Country: {country}")

        try:
            if "schedule" in self.page.url:
                logger.info("Already on schedule page, skipping landing navigation")
                return True

            await self.page.get(config.BASE_URL)
            await asyncio.sleep(3)

            # Wait for Angular
            for i in range(20):
                has_input = await self.page.evaluate(
                    "document.querySelector(\"input[placeholder='-- Select Language --']\") !== null"
                )
                if has_input:
                    logger.info(f"Angular ready after {i+1} checks")
                    break
                await asyncio.sleep(2)

            # CDP click the input to open dropdown
            coords = await self.page.evaluate("""
                (() => {
                    const input = document.querySelector("input[placeholder='-- Select Language --']");
                    const rect = input.getBoundingClientRect();
                    return {x: rect.x + rect.width/2, y: rect.y + rect.height/2};
                })()
            """)

            if isinstance(coords, list):
                ix = next(v[1]['value'] if isinstance(v[1], dict) else v[1] for v in coords if v[0] == 'x')
                iy = next(v[1]['value'] if isinstance(v[1], dict) else v[1] for v in coords if v[0] == 'y')
            else:
                ix, iy = coords['x'], coords['y']

            await self.page.send(cdp.input_.dispatch_mouse_event(
                type_="mousePressed", x=float(ix), y=float(iy),
                button=cdp.input_.MouseButton("left"), click_count=1
            ))
            await asyncio.sleep(0.05)
            await self.page.send(cdp.input_.dispatch_mouse_event(
                type_="mouseReleased", x=float(ix), y=float(iy),
                button=cdp.input_.MouseButton("left"), click_count=1
            ))
            await asyncio.sleep(2)

            # DIAGNOSTIC: Dump EVERYTHING about dropdowns on the page
            diag = await self.page.evaluate("""
                (() => {
                    const results = {};
                    
                    // 1. All dropdown-menu elements
                    const menus = document.querySelectorAll('.dropdown-menu');
                    results.menuCount = menus.length;
                    results.menus = [];
                    menus.forEach((menu, i) => {
                        const style = window.getComputedStyle(menu);
                        const rect = menu.getBoundingClientRect();
                        results.menus.push({
                            index: i,
                            display: style.display,
                            visibility: style.visibility,
                            height: rect.height,
                            innerHTML: menu.innerHTML.substring(0, 500),
                            childCount: menu.children.length,
                            aCount: menu.querySelectorAll('a').length,
                            liCount: menu.querySelectorAll('li').length,
                            allText: menu.textContent.substring(0, 200)
                        });
                    });
                    
                    // 2. All <a> tags on page
                    const allAs = document.querySelectorAll('a');
                    results.totalAnchorCount = allAs.length;
                    results.anchorTexts = Array.from(allAs).map(a => ({
                        text: a.textContent.trim(),
                        visible: a.offsetHeight > 0,
                        parent: a.parentElement?.tagName,
                        grandparent: a.parentElement?.parentElement?.tagName
                    }));
                    
                    // 3. The specific dropdown container
                    const input = document.querySelector("input[placeholder='-- Select Language --']");
                    const dropdown = input?.closest('.dropdown');
                    if (dropdown) {
                        results.dropdownHTML = dropdown.outerHTML.substring(0, 1000);
                        results.dropdownChildren = dropdown.children.length;
                    }
                    
                    // 4. Check aria-expanded
                    results.ariaExpanded = input?.getAttribute('aria-expanded');
                    
                    return results;
                })()
            """)
            logger.info(f"=== DIAGNOSTIC DUMP ===")
            logger.info(f"{json.dumps(diag, indent=2, default=str)}")
            logger.info(f"=== END DIAGNOSTIC ===")

            # Don't proceed - just return False so we can see the diagnostic
            return False

        except Exception as e:
            logger.error(f"Landing page navigation failed: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    async def _get_captcha_image(self) -> Optional[str]:
        try:
            img_element = await self._wait_for(selectors.CAPTCHA_IMAGE)

            if not img_element:
                logger.warning("Main CAPTCHA selector failed, trying fallbacks...")
                for fallback_sel in ["img[id*='aptcha']", "img[src*='base64']"]:
                    img_element = await self._wait_for(fallback_sel, timeout=3)
                    if img_element:
                        logger.info(f"Found CAPTCHA with fallback: {fallback_sel}")
                        break

            if not img_element:
                logger.error("CAPTCHA image element not found.")
                return None

            src = img_element.attrs.get("src")
            if not src:
                logger.debug("CAPTCHA src not in attributes, trying eval...")
                src = await img_element.eval("this.src")

            if src and "base64," in src:
                logger.info("CAPTCHA base64 data extracted successfully.")
                return src.split("base64,")[1]

            logger.error(f"CAPTCHA src invalid or not base64: {str(src)[:50]}...")
            return None

        except Exception as e:
            logger.error(f"Failed to extract CAPTCHA: {e}")
            return None

    async def _refresh_captcha(self) -> bool:
        try:
            refresh_selectors = [
                ".refresh-icon",
                "[class*='refresh']",
                "div.captchablock + div",
                "img[id='captchaImage'] + div"
            ]
            for sel in refresh_selectors:
                if await self._click(sel):
                    await asyncio.sleep(1)
                    return True
            return False
        except:
            return False

    async def solve_captcha(self) -> Optional[str]:
        for attempt in range(config.CAPTCHA_MAX_RETRIES):
            logger.info(f"CAPTCHA attempt {attempt + 1}/{config.CAPTCHA_MAX_RETRIES}")

            image_b64 = await self._get_captcha_image()
            if not image_b64:
                await self._refresh_captcha()
                continue

            solution = await self.solver.solve(image_b64)
            if solution:
                return solution

            await self._refresh_captcha()
            await asyncio.sleep(1)

        return None

    async def _check_login_success(self) -> bool:
        if "applicantdetails" in self.page.url:
            return True

        try:
            logout = await self.page.select(selectors.LOGOUT_BTN, timeout=1)
            if logout:
                return True
        except:
            pass

        try:
            await self.page.wait_for("qvc-applicantdetails", timeout=1)
            return True
        except:
            pass

        return False

    async def _check_active_session_popup(self) -> Optional[uc.Element]:
        try:
            ok_btn = await self.page.select(selectors.SESSION_ACTIVE_OK_BTN, timeout=2)
            if ok_btn:
                return ok_btn
        except:
            pass

        try:
            ok_btn = await self.page.find(selectors.SESSION_ACTIVE_OK_BTN_XPATH, timeout=1)
            if ok_btn:
                return ok_btn
        except:
            pass

        return None

    async def _check_captcha_error(self) -> bool:
        try:
            error_selectors = [
                ".error",
                ".alert-danger",
                "[class*='error']",
                ".invalid-feedback",
                "span.text-danger"
            ]
            for sel in error_selectors:
                error_el = await self.page.select(sel, timeout=0.5)
                if error_el:
                    error_text = await error_el.eval("this.textContent")
                    if error_text and "captcha" in error_text.lower():
                        logger.warning(f"CAPTCHA error detected: {error_text.strip()}")
                        return True
        except:
            pass
        return False

    async def _clear_captcha_input(self) -> bool:
        try:
            captcha_input = await self._wait_for(selectors.CAPTCHA_INPUT, timeout=3)
            if captcha_input:
                await captcha_input.click()
                await asyncio.sleep(0.1)
                await captcha_input.clear_input()
                return True
        except:
            pass

        try:
            captcha_input = await self.page.find(selectors.CAPTCHA_INPUT_XPATH, timeout=2)
            if captcha_input:
                await captcha_input.click()
                await asyncio.sleep(0.1)
                await captcha_input.clear_input()
                return True
        except:
            pass

        return False

    async def _solve_and_fill_captcha(self) -> bool:
        await self._clear_captcha_input()
        await asyncio.sleep(0.3)

        captcha_solution = await self.solve_captcha()
        if not captcha_solution:
            logger.error("Failed to solve CAPTCHA")
            return False

        logger.info(f"CAPTCHA solved: {captcha_solution}")

        if await self._type(selectors.CAPTCHA_INPUT, captcha_solution):
            return True

        logger.warning("CAPTCHA input (CSS) failed, trying XPath...")
        if await self._type_xpath(selectors.CAPTCHA_INPUT_XPATH, captcha_solution):
            return True

        logger.error("Failed to enter CAPTCHA solution")
        return False

    async def _click_submit(self) -> bool:
        try:
            xpath_btn = await self.page.find(selectors.SUBMIT_BTN_XPATH, timeout=5)
            if xpath_btn:
                await xpath_btn.click()
                return True
        except:
            pass

        if await self._click(selectors.SUBMIT_BTN):
            return True

        logger.error("Failed to click submit button")
        return False

    async def login(self, applicant: Applicant) -> bool:
        logger.info(f"Logging in: {applicant.passport_number}")

        try:
            if "schedule" not in self.page.url:
                if not await self.navigate_landing_page(applicant.country):
                    logger.error("Failed to navigate landing page")
                    return False

            await asyncio.sleep(2)

            try:
                popup_close = await self._wait_for(selectors.POPUP_CLOSE_BTN, timeout=3)
                if popup_close:
                    logger.info("Closing attention popup")
                    await popup_close.click()
                    await asyncio.sleep(1)
            except:
                pass

            logger.info("Filling passport number...")
            if not await self._type(selectors.PASSPORT_INPUT, applicant.passport_number):
                if not await self._type_xpath(selectors.PASSPORT_INPUT_XPATH, applicant.passport_number):
                    logger.error("Failed to enter passport number")
                    return False

            logger.info("Filling visa number...")
            if not await self._type(selectors.VISA_INPUT, applicant.visa_number):
                if not await self._type_xpath(selectors.VISA_INPUT_XPATH, applicant.visa_number):
                    logger.error("Failed to enter visa number")
                    return False

            max_attempts = 5
            for attempt in range(1, max_attempts + 1):
                logger.info(f"=== Login attempt {attempt}/{max_attempts} ===")

                if not await self._solve_and_fill_captcha():
                    logger.error(f"Attempt {attempt}: CAPTCHA solving failed")
                    if self.proxy_manager:
                        await self.proxy_manager.report_failure("captcha")
                    if attempt < max_attempts:
                        await asyncio.sleep(1)
                        continue
                    return False

                await asyncio.sleep(0.3)

                logger.info("Clicking submit...")
                if not await self._click_submit():
                    logger.error("Failed to click submit")
                    return False

                logger.info("Waiting for server response...")
                await asyncio.sleep(2.5)

                if await self._check_login_success():
                    logger.info("✓ Login successful!")
                    if self.proxy_manager:
                        await self.proxy_manager.report_success()
                    return True

                ok_button = await self._check_active_session_popup()
                if ok_button:
                    logger.warning("Active Session popup detected!")
                    await ok_button.click()
                    await asyncio.sleep(3)
                    logger.info("Session cleared. Will re-solve CAPTCHA...")
                    continue

                if await self._check_captcha_error():
                    logger.warning("CAPTCHA was incorrect, retrying...")
                    continue

                try:
                    error_el = await self.page.select(".error, .alert-danger", timeout=1)
                    if error_el:
                        error_text = await error_el.eval("this.textContent")
                        logger.error(f"Login error: {error_text.strip()}")
                        return False
                except:
                    pass

                await asyncio.sleep(1)
                if await self._check_login_success():
                    logger.info("✓ Login successful (delayed)!")
                    if self.proxy_manager:
                        await self.proxy_manager.report_success()
                    return True

                logger.warning(f"Attempt {attempt}: No clear result, retrying...")

            logger.error(f"Login failed after {max_attempts} attempts")
            return False

        except Exception as e:
            logger.error(f"Login failed with exception: {e}")
            import traceback
            traceback.print_exc()
            return False

    async def _handle_notification_popup(self) -> bool:
        logger.info("Checking for notification popup...")
        try:
            await asyncio.sleep(1)

            close_btn = await self._wait_for(selectors.NOTIFICATION_POPUP_CLOSE_BTN, timeout=3)
            if close_btn:
                logger.info("Found notification popup (CSS), closing...")
                await close_btn.click()
                await asyncio.sleep(1)
                return True

            close_btn = await self.page.find(selectors.NOTIFICATION_POPUP_CLOSE_BTN_XPATH, timeout=2)
            if close_btn:
                logger.info("Found notification popup (XPath), closing...")
                await close_btn.click()
                await asyncio.sleep(1)
                return True

            logger.debug("No notification popup found")
            return False

        except Exception as e:
            logger.debug(f"Notification popup check failed: {e}")
            return False

    async def fill_contact_details(self, applicant: Applicant) -> bool:
        logger.info("Filling contact details...")

        try:
            await asyncio.sleep(2)
            await self._handle_notification_popup()

            if not await self._type(selectors.PRIMARY_MOBILE, applicant.mobile):
                logger.warning("Primary mobile (CSS) failed, trying XPath...")
                await self._type_xpath(selectors.PRIMARY_MOBILE_XPATH, applicant.mobile)

            if not await self._type(selectors.PRIMARY_EMAIL, applicant.email):
                logger.warning("Primary email (CSS) failed, trying XPath...")
                await self._type_xpath(selectors.PRIMARY_EMAIL_XPATH, applicant.email)

            if not await self._type(selectors.APPLICANT_MOBILE, applicant.mobile):
                logger.warning("Applicant mobile (CSS) failed, trying XPath...")
                await self._type_xpath(selectors.APPLICANT_MOBILE_XPATH, applicant.mobile)

            if not await self._type(selectors.APPLICANT_EMAIL, applicant.email):
                logger.warning("Applicant email (CSS) failed, trying XPath...")
                await self._type_xpath(selectors.APPLICANT_EMAIL_XPATH, applicant.email)

            logger.info("Contact details filled")

            if await self._click(selectors.CONFIRM_DETAILS_BTN):
                logger.info("Confirm button clicked (CSS)")
                await asyncio.sleep(2)
                return True

            logger.warning("Confirm button (CSS) failed, trying XPath...")
            confirm_btn = await self.page.find(selectors.CONFIRM_DETAILS_BTN_XPATH, timeout=5)
            if confirm_btn:
                await confirm_btn.click()
                logger.info("Confirm button clicked (XPath)")
                await asyncio.sleep(2)
                return True

            logger.error("Failed to click Confirm button")
            return False

        except Exception as e:
            logger.error(f"Failed to fill contact details: {e}")
            return False

    async def _handle_slot_notification_popup(self) -> bool:
        logger.info("Checking for slot notification popup...")
        try:
            await asyncio.sleep(1)

            close_btn = await self._wait_for(selectors.SLOT_NOTIFICATION_CLOSE_BTN, timeout=3)
            if close_btn:
                logger.info("Found slot notification popup (CSS), closing...")
                await close_btn.click()
                await asyncio.sleep(1)
                return True

            close_btn = await self.page.find(selectors.SLOT_NOTIFICATION_CLOSE_BTN_XPATH, timeout=2)
            if close_btn:
                logger.info("Found slot notification popup (XPath), closing...")
                await close_btn.click()
                await asyncio.sleep(1)
                return True

            logger.debug("No slot notification popup found")
            return False

        except Exception as e:
            logger.debug(f"Slot notification popup check failed: {e}")
            return False

    async def find_available_slot(
        self,
        start_date: date,
        end_date: date,
        poll_interval: float = 2.0,
        max_duration: int = 3600,
        center: str = "Islamabad"
    ) -> Optional[Tuple[date, str]]:
        logger.info(f"Starting slot hunt: {start_date} to {end_date}")
        logger.info(f"Poll interval: {poll_interval}s, Max duration: {max_duration}s")

        await self._handle_slot_notification_popup()

        from slot_monitor import SlotHunter, CapturedSlot

        hunter = SlotHunter(
            page=self.page,
            target_center=center,
            poll_interval=poll_interval,
            max_poll_duration=max_duration,
            date_range=(start_date, end_date),
            proxy_manager=self.proxy_manager,
            browser_engine=None,
        )

        result = await hunter.hunt()

        if result:
            logger.info(f"Slot captured: {result.date} at {result.time}")
            return (result.date, result.time)

        logger.warning("Slot hunting finished without finding a slot")
        return None

    async def _select_time_slot(self) -> Optional[str]:
        try:
            slots = await self.page.select_all(selectors.TIME_SLOT)

            if not slots:
                slots = await self.page.select_all(".slot:not(.disabled), .time:not(.booked)")

            if slots:
                first_slot = slots[0]
                slot_text = await first_slot.eval("this.textContent")
                await first_slot.click()
                logger.info(f"Selected time slot: {slot_text}")
                return slot_text.strip()

            return None

        except Exception as e:
            logger.error(f"Failed to select time slot: {e}")
            return None

    async def confirm_booking(self) -> bool:
        try:
            if await self._click(selectors.CONFIRM_BTN):
                await asyncio.sleep(3)

                success_selectors = [
                    ".success",
                    ".alert-success",
                    "[class*='confirm']",
                    ":has-text('successfully')"
                ]

                for sel in success_selectors:
                    if await self._wait_for(sel, timeout=3):
                        logger.info("Booking confirmed!")
                        return True

                if "confirm" in self.page.url.lower() or "success" in self.page.url.lower():
                    logger.info("Booking appears confirmed (URL)")
                    return True

            return False

        except Exception as e:
            logger.error(f"Booking confirmation failed: {e}")
            return False

    async def book_appointment(
        self,
        applicant: Applicant,
        start_date: date,
        end_date: date,
        poll_interval: float = 2.0,
        max_hunt_duration: int = 3600,
        center: str = "Islamabad"
    ) -> bool:
        max_full_retries = 3

        for retry in range(max_full_retries):
            logger.info("=" * 60)
            logger.info(f"BOOKING ATTEMPT {retry + 1}/{max_full_retries}")
            logger.info(f"Applicant: {applicant.passport_number}")
            logger.info(f"Date range: {start_date} to {end_date}")
            logger.info(f"Center: {center}")
            if self.proxy_manager and self._current_proxy:
                logger.info(f"Proxy session: {self._current_proxy.session_id}")
            logger.info("=" * 60)

            try:
                if not await self.login(applicant):
                    logger.error("Login failed")
                    if self.proxy_manager:
                        rotated = await self.proxy_manager.report_failure("captcha")
                        if rotated and retry < max_full_retries - 1:
                            logger.info("Rotating IP and retrying...")
                            await self.restart_with_new_ip()
                    continue

                if not await self.fill_contact_details(applicant):
                    logger.error("Contact details failed")
                    if retry < max_full_retries - 1:
                        logger.info("Retrying from beginning...")
                        await self.page.get(config.BASE_URL)
                        await asyncio.sleep(2)
                    continue

                slot = await self.find_available_slot(
                    start_date=start_date,
                    end_date=end_date,
                    poll_interval=poll_interval,
                    max_duration=max_hunt_duration,
                    center=center
                )

                if not slot:
                    logger.info("=" * 60)
                    logger.info("⏰ SCHEDULE WINDOW COMPLETED - No slots detected")
                    logger.info(f"Applicant: {applicant.passport_number}")
                    logger.info(f"Searched: {start_date} to {end_date}")
                    logger.info(f"Center: {center}")
                    logger.info("=" * 60)
                    return False

                slot_date, slot_time = slot

                logger.info("=" * 60)
                logger.info("🎉 SLOT DETECTION SUCCESSFUL!")
                logger.info(f"Applicant: {applicant.passport_number}")
                logger.info(f"Detected date: {slot_date}")
                logger.info(f"Center: {center}")
                logger.info("✓ Detection complete - browser will close")
                logger.info("=" * 60)

                return True

            except Exception as e:
                logger.error(f"Detection attempt {retry + 1} failed with exception: {e}")
                import traceback
                traceback.print_exc()

                if self.proxy_manager:
                    rotated = await self._handle_request_error(e)
                    if rotated:
                        logger.info("IP rotated due to error - retrying...")
                        continue

                if retry < max_full_retries - 1:
                    await asyncio.sleep(3)

        logger.error(f"All {max_full_retries} detection attempts failed for {applicant.passport_number}")
        return False

    async def screenshot(self, filename: str = "debug.png"):
        if self.page:
            try:
                await self.page.save_screenshot(filename)
                logger.info(f"Screenshot saved: {filename}")
            except Exception as e:
                logger.warning(f"Screenshot failed: {e}")
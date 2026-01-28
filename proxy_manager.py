"""
Proxy rotation manager for Data Impulse residential proxies
"""

import asyncio
import random
import string
import logging
from dataclasses import dataclass
from typing import Optional
import httpx

logger = logging.getLogger(__name__)


@dataclass
class ProxyConfig:
    host: str
    port: int
    username: str
    password: str
    
    # Session ID for tracking (only used if use_sticky_session=True)
    session_id: Optional[str] = None
    
    # Whether to append session suffix (only for Sticky mode in dashboard)
    use_sticky_session: bool = False
    
    @property
    def session_username(self) -> str:
        """
        Username for proxy auth.
        
        Data Impulse modes:
        - Rotating: use plain login (each request = new IP)
        - Sticky: use login-session-XXXX (same IP for session duration)
        """
        if self.use_sticky_session and self.session_id:
            return f"{self.username}-session-{self.session_id}"
        return self.username
    
    @property
    def url(self) -> str:
        """Full proxy URL for httpx"""
        return f"http://{self.session_username}:{self.password}@{self.host}:{self.port}"
    
    @property
    def chrome_args(self) -> list:
        """Chrome launch arguments for proxy"""
        return [f"--proxy-server=http://{self.host}:{self.port}"]
    
    def rotate(self) -> 'ProxyConfig':
        """Generate new session ID (for tracking and sticky mode)"""
        new_session = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
        logger.info(f"Rotating proxy session: {self.session_id} -> {new_session}")
        return ProxyConfig(
            host=self.host,
            port=self.port,
            username=self.username,
            password=self.password,
            session_id=new_session,
            use_sticky_session=self.use_sticky_session
        )


class ProxyManager:
    """
    Manages proxy rotation with Data Impulse residential proxies.
    
    Modes:
    - Rotating (default): Each browser restart = new IP automatically
    - Sticky: Same IP maintained via session suffix (requires dashboard setting)
    
    Rotation triggers:
    - HTTP 429 (rate limited)
    - Connection refused/timeout
    - CAPTCHA solve fails 3+ times consecutively
    - Manual rotation request
    """
    
    # Data Impulse endpoints
    DEFAULT_HOST = "gw.dataimpulse.com"
    DEFAULT_PORT = 823  # HTTP port
    
    def __init__(
        self,
        username: str,
        password: str,
        host: str = None,
        port: int = None,
        sticky_duration_mins: int = 10,
        max_rotations_per_session: int = 20,
        use_sticky_session: bool = False,  # Set True ONLY if "Sticky" is enabled in Data Impulse dashboard
    ):
        self.base_config = ProxyConfig(
            host=host or self.DEFAULT_HOST,
            port=port or self.DEFAULT_PORT,
            username=username,
            password=password,
            use_sticky_session=use_sticky_session,
        )
        self.sticky_duration = sticky_duration_mins
        self.max_rotations = max_rotations_per_session
        
        self._current: Optional[ProxyConfig] = None
        self._rotation_count = 0
        self._consecutive_failures = 0
        self._lock = asyncio.Lock()
    
    @property
    def current(self) -> ProxyConfig:
        """Get current proxy config, initialize if needed"""
        if not self._current:
            self._current = self.base_config.rotate()
            self._rotation_count = 1
        return self._current
    
    async def rotate(self, reason: str = "manual") -> ProxyConfig:
        """Force rotation to new IP"""
        async with self._lock:
            if self._rotation_count >= self.max_rotations:
                logger.warning(f"Max rotations ({self.max_rotations}) reached this session")
                # Continue anyway, just log
            
            self._current = self.base_config.rotate()
            self._rotation_count += 1
            self._consecutive_failures = 0
            
            logger.info(f"Proxy rotated (reason: {reason}) | Count: {self._rotation_count} | Session: {self._current.session_id}")
            return self._current
    
    async def report_success(self):
        """Call after successful request"""
        self._consecutive_failures = 0
    
    async def report_failure(self, error_type: str) -> bool:
        """
        Call after failed request. Returns True if rotation was triggered.
        
        Args:
            error_type: 'rate_limit', 'connection', 'captcha', 'blocked'
        """
        self._consecutive_failures += 1
        
        # Immediate rotation triggers
        immediate_rotate = {'rate_limit', 'blocked', 'connection'}
        
        if error_type in immediate_rotate:
            await self.rotate(reason=error_type)
            return True
        
        # CAPTCHA failures - rotate after 3 consecutive
        if error_type == 'captcha' and self._consecutive_failures >= 3:
            await self.rotate(reason="captcha_loop")
            return True
        
        return False
    
    async def verify_ip(self) -> Optional[str]:
        """Verify current proxy IP via external service"""
        try:
            proxy_url = self.current.url
            
            # httpx 0.24+ uses 'proxy' parameter, older versions use 'proxies'
            # Try the newer syntax first, fall back to mounts if needed
            try:
                # Newer httpx (0.24+) - single proxy parameter
                async with httpx.AsyncClient(
                    proxy=proxy_url,
                    timeout=15
                ) as client:
                    response = await client.get("https://api.ipify.org?format=json")
                    if response.status_code == 200:
                        ip = response.json().get("ip")
                        logger.info(f"Current proxy IP: {ip}")
                        return ip
            except TypeError:
                # Older httpx - use mounts
                mounts = {
                    "http://": httpx.AsyncHTTPTransport(proxy=proxy_url),
                    "https://": httpx.AsyncHTTPTransport(proxy=proxy_url),
                }
                async with httpx.AsyncClient(mounts=mounts, timeout=15) as client:
                    response = await client.get("https://api.ipify.org?format=json")
                    if response.status_code == 200:
                        ip = response.json().get("ip")
                        logger.info(f"Current proxy IP: {ip}")
                        return ip
                        
        except httpx.ProxyError as e:
            logger.error(f"Proxy connection failed: {e}")
        except httpx.TimeoutException:
            logger.error("Proxy verification timed out")
        except Exception as e:
            logger.error(f"IP verification failed: {e}")
        return None
    
    async def test_connection(self) -> dict:
        """
        Test proxy connection and return detailed status.
        Useful for debugging.
        """
        result = {
            "success": False,
            "ip": None,
            "session_id": self.current.session_id,
            "proxy_url": f"{self.current.host}:{self.current.port}",
            "error": None
        }
        
        try:
            ip = await self.verify_ip()
            if ip:
                result["success"] = True
                result["ip"] = ip
            else:
                result["error"] = "Could not retrieve IP"
        except Exception as e:
            result["error"] = str(e)
        
        return result
    
    def get_stats(self) -> dict:
        return {
            "current_session": self._current.session_id if self._current else None,
            "rotation_count": self._rotation_count,
            "consecutive_failures": self._consecutive_failures,
        }


# Quick test function
async def _test_proxy():
    """Standalone test - run with: python proxy_manager.py"""
    import sys
    
    if len(sys.argv) >= 3:
        username = sys.argv[1]
        password = sys.argv[2]
    else:
        # Try to load from config
        try:
            from config import config
            username = config.PROXY_USERNAME
            password = config.PROXY_PASSWORD
            print("Loaded credentials from config.py")
        except:
            print("Usage: python proxy_manager.py <login> <password>")
            print("Or set credentials in config.py")
            return
    
    print("=" * 50)
    print("Data Impulse Proxy Test")
    print("=" * 50)
    
    # use_sticky_session=False for Rotating mode (default in dashboard)
    pm = ProxyManager(
        username=username,
        password=password,
        use_sticky_session=False
    )
    
    print(f"Host: {pm.base_config.host}:{pm.base_config.port}")
    print(f"Login: {pm.base_config.username}")
    print(f"Mode: {'Sticky' if pm.base_config.use_sticky_session else 'Rotating'}")
    print()
    
    print("Testing connection...")
    result = await pm.test_connection()
    
    if result["success"]:
        print(f"✓ Proxy working!")
        print(f"  Your IP: {result['ip']}")
    else:
        print(f"✗ Proxy failed!")
        print(f"  Error: {result['error']}")
        print()
        print("Troubleshooting:")
        print("  1. Check credentials match Data Impulse dashboard exactly")
        print("  2. Make sure you have traffic left (5 GB shown in dashboard)")
        print("  3. Try port 824 if 823 doesn't work")
        return
    
    print()
    print("Testing second request (Rotating mode = may get different IP)...")
    
    ip2 = await pm.verify_ip()
    if ip2:
        if ip2 != result["ip"]:
            print(f"✓ Different IP: {ip2}")
        else:
            print(f"  Same IP: {ip2} (normal for quick requests)")
    
    print()
    print("=" * 50)
    print("Proxy test complete!")


if __name__ == "__main__":
    asyncio.run(_test_proxy())
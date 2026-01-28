from config import config

def verify():
    print("=== Config Verification ===")
    print(f"Proxy Enabled: {config.PROXY_ENABLED}")
    print(f"Proxy Host:    {config.PROXY_HOST}")
    print(f"Proxy Port:    {config.PROXY_PORT}")
    print(f"Proxy User:    {config.PROXY_USERNAME}")
    # Mask password for safety
    pwd = config.PROXY_PASSWORD
    masked_pwd = pwd[:2] + "*" * (len(pwd) - 4) + pwd[-2:] if len(pwd) > 4 else "****"
    print(f"Proxy Pass:    {masked_pwd}")
    print(f"Sticky Mins:   {config.PROXY_STICKY_MINS}")
    print(f"Max Rotations: {config.PROXY_MAX_ROTATIONS}")
    print("==========================")

if __name__ == "__main__":
    verify()

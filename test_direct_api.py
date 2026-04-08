import requests
import time
import hmac
import hashlib
import base64
import json

# 钱包地址
address = "0xBceC747B92F6da7d4469be15eaBEA927CB88E6Df"
api_key = "128dc9d1-de1f-2f81-6620-7b5e96124d05"
api_secret = "1MMjS33p39XeVZwgzo_5djrgeRErf2dglPTaM_DQ9OQ="
passphrase = "10320352192155cc7f6ad32f63a99363a9fe4df13c4225a208ce1c8ef6bbe5ac"

# 创建 HMAC 签名
def create_signature(secret, timestamp, method, path):
    message = f"{timestamp}{method.upper()}{path}"
    mac = hmac.new(base64.b64decode(secret), message.encode(), hashlib.sha256)
    return base64.b64encode(mac.digest()).decode()

# 测试余额 API
timestamp = str(int(time.time()))
path = "/balance"

headers = {
    "POLY_ADDRESS": address,
    "POLY_API_KEY": api_key,
    "POLY_PASSPHRASE": passphrase,
    "POLY_TIMESTAMP": timestamp,
    "POLY_SIGNATURE": create_signature(api_secret, timestamp, "GET", path),
}

print(f"Testing balance API...")
print(f"URL: https://clob.polymarket.com{path}")
print(f"Headers: {json.dumps({k: v[:20] + '...' if len(v) > 20 else v for k, v in headers.items()}, indent=2)}")

try:
    response = requests.get(f"https://clob.polymarket.com{path}", headers=headers)
    print(f"\nStatus: {response.status_code}")
    print(f"Response: {response.text}")
except Exception as e:
    print(f"Error: {e}")

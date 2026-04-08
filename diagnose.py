"""
诊断脚本 - 检查 Polymarket 配置和余额
"""
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# 加载 .env 文件
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    load_dotenv(env_path)
    print(f"[OK] 已加载 .env 文件")
else:
    print(f"[!] 未找到 .env 文件: {env_path}")
    sys.exit(1)

print("\n" + "=" * 60)
print("配置检查")
print("=" * 60)

# 检查必要的环境变量
required_vars = [
    "PRIVATE_KEY",
    "API_KEY",
    "API_SECRET",
    "PASSPHRASE",
]

for var in required_vars:
    value = os.getenv(var, "")
    if value:
        if var == "PRIVATE_KEY":
            print(f"[OK] {var}: 已设置 (长度: {len(value)})")
        else:
            print(f"[OK] {var}: 已设置 (长度: {len(value)})")
    else:
        print(f"[X] {var}: 未设置")

# 可选变量
optional_vars = ["SIGNATURE_TYPE", "FUNDER_ADDRESS", "CHAIN_ID"]
for var in optional_vars:
    value = os.getenv(var, "")
    print(f"[*] {var}: {value if value else '未设置'}")

# 检查私钥对应的地址
print("\n" + "=" * 60)
print("私钥检查")
print("=" * 60)

private_key = os.getenv("PRIVATE_KEY", "")
if private_key:
    try:
        from eth_account import Account
        
        # 移除 0x 前缀
        if private_key.startswith("0x"):
            private_key = private_key[2:]
        
        acct = Account.from_key(private_key)
        print(f"[OK] 私钥对应的地址: {acct.address}")
        
        # 检查 funder_address 是否匹配
        funder = os.getenv("FUNDER_ADDRESS", "")
        if funder:
            if funder.lower() == acct.address.lower():
                print(f"[OK] FUNDER_ADDRESS 与私钥地址匹配")
            else:
                print(f"[!] FUNDER_ADDRESS ({funder}) 与私钥地址 ({acct.address}) 不匹配!")
        else:
            print(f"[*] FUNDER_ADDRESS 未设置，将使用私钥地址")
            
    except Exception as e:
        print(f"[X] 私钥解析失败: {e}")
else:
    print("[X] PRIVATE_KEY 未设置")

# 查询链上余额
print("\n" + "=" * 60)
print("链上余额检查")
print("=" * 60)

if private_key:
    try:
        import requests
        
        # 获取查询地址
        address = os.getenv("FUNDER_ADDRESS", "")
        if not address:
            from eth_account import Account
            if private_key.startswith("0x"):
                private_key_clean = private_key[2:]
            else:
                private_key_clean = private_key
            acct = Account.from_key(private_key_clean)
            address = acct.address
        
        print(f"[*] 查询地址: {address}")
        
        # 查询 USDC.e 余额 (Polygon)
        # USDC.e 合约: 0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174
        url = "https://polygon-rpc.com"
        payload = {
            "jsonrpc": "2.0",
            "method": "eth_call",
            "params": [{
                "to": "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
                "data": f"0x70a08231000000000000000000000000{address[2:]}"
            }, "latest"],
            "id": 1
        }
        
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            result = response.json().get("result", "0x0")
            balance_wei = int(result, 16)
            balance_usdc = balance_wei / 1000000
            print(f"[OK] USDC.e 余额 (Polygon): {balance_usdc:.6f} USDC")
        else:
            print(f"[X] 查询失败: {response.status_code}")
            
        # 查询 MATIC 余额
        payload_matic = {
            "jsonrpc": "2.0",
            "method": "eth_getBalance",
            "params": [address, "latest"],
            "id": 1
        }
        
        response = requests.post(url, json=payload_matic, timeout=10)
        if response.status_code == 200:
            result = response.json().get("result", "0x0")
            matic_wei = int(result, 16)
            matic = matic_wei / 1e18
            print(f"[OK] MATIC 余额: {matic:.6f} MATIC")
            
    except Exception as e:
        print(f"[X] 余额查询失败: {e}")

# 测试 API 连接
print("\n" + "=" * 60)
print("API 连接测试")
print("=" * 60)

try:
    from polymarket_api import PolymarketClient
    
    client = PolymarketClient(
        private_key=os.getenv("PRIVATE_KEY", ""),
        api_key=os.getenv("API_KEY", ""),
        api_secret=os.getenv("API_SECRET", ""),
        passphrase=os.getenv("PASSPHRASE", ""),
        signature_type=int(os.getenv("SIGNATURE_TYPE", "0")),
        funder_address=os.getenv("FUNDER_ADDRESS", ""),
    )
    
    print("[OK] API 客户端初始化成功")
    
    # 获取余额
    print("\n[*] 正在通过 SDK 获取余额...")
    balance = client.get_balance()
    print(f"[OK] SDK 余额: ${balance:.2f}")
    
except Exception as e:
    print(f"[X] API 连接失败: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 60)
print("诊断完成")
print("=" * 60)

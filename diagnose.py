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
    print(f"[*] 正在创建默认 .env 文件...")
    sys.exit(1)

print("\n" + "=" * 60)
print("配置检查")
print("=" * 60)

# 检查必要的环境变量
required_vars = [
    ("PRIVATE_KEY", "私钥"),
    ("SIGNATURE_TYPE", "签名类型"),
]

for var, desc in required_vars:
    value = os.getenv(var, "")
    if var == "PRIVATE_KEY":
        print(f"[{'OK' if value else 'X'}] {desc}: {'已设置' if value else '未设置'}")
    else:
        print(f"[{'OK' if value else '!'}] {desc}: {value if value else '未设置'}")

# 检查可选变量
optional_vars = [
    ("FUNDER_ADDRESS", "资金地址"),
    ("API_KEY", "API Key"),
    ("API_SECRET", "API Secret"),
    ("PASSPHRASE", "Passphrase"),
]
for var, desc in optional_vars:
    value = os.getenv(var, "")
    print(f"[*] {desc}: {value if value else '未设置'}")

# 检查私钥对应的地址
print("\n" + "=" * 60)
print("钱包检查")
print("=" * 60)

private_key = os.getenv("PRIVATE_KEY", "")
signature_type = int(os.getenv("SIGNATURE_TYPE", "0"))
funder_address = os.getenv("FUNDER_ADDRESS", "")

if private_key:
    try:
        from eth_account import Account
        
        # 移除 0x 前缀
        if private_key.startswith("0x"):
            private_key = private_key[2:]
        
        acct = Account.from_key(private_key)
        print(f"[OK] 签名密钥地址: {acct.address}")
        
        # 检查签名类型
        type_names = {0: "EOA (普通钱包)", 1: "POLY_PROXY", 2: "GNOSIS_SAFE (Safe 多签钱包)"}
        print(f"[*] 签名类型: {signature_type} - {type_names.get(signature_type, '未知')}")
        
        if signature_type == 2:
            print(f"[*] 资金地址: {funder_address if funder_address else '未设置'}")
            if not funder_address:
                print("\n[!!!] 错误: Safe 钱包必须设置 FUNDER_ADDRESS!")
                print("     请在 .env 中设置: FUNDER_ADDRESS=0x你的Safe钱包地址")
            elif funder_address.lower() == acct.address.lower():
                print("\n[!] 警告: 资金地址与签名密钥地址相同!")
                print("    Safe 钱包的资金地址应该与签名密钥地址不同")
            else:
                print("\n[OK] Safe 钱包配置正确")
                print(f"    签名密钥: {acct.address}")
                print(f"    资金地址: {funder_address}")
        elif signature_type == 0:
            print(f"[*] 普通钱包模式，余额地址: {acct.address}")
            
    except Exception as e:
        print(f"[X] 私钥解析失败: {e}")
else:
    print("[X] 私钥未设置")

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
    print("\n[*] 正在获取余额...")
    balance = client.get_balance()
    print(f"[OK] 账户余额: ${balance:.2f}")
    
    # 健康检查
    if client.health_check():
        print("[OK] API 连接正常")
    else:
        print("[!] API 连接异常")
    
except Exception as e:
    print(f"[X] API 连接失败: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 60)
print("诊断完成")
print("=" * 60)

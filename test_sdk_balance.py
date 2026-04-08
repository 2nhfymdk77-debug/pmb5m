import sys
sys.path.insert(0, ".")
from polymarket_api import PolymarketClient
import inspect

client = PolymarketClient(
    private_key='0x8820445ec69f6e4526081be00bfa5ee1c5c9d1cf21f3bbeecbe1ceeb18878d5e',
    api_key='128dc9d1-de1f-2f81-6620-7b5e96124d05',
    api_secret='1MMjS33p39XeVZwgzo_5djrgeRErf2dglPTaM_DQ9OQ=',
    passphrase='10320352192155cc7f6ad32f63a99363a9fe4df13c4225a208ce1c8ef6bbe5ac',
    funder_address='0xBceC747B92F6da7d4469be15eaBEA927CB88E6Df'
)

# 获取 SDK 的 get_balance 方法源码
print("Checking SDK get_balance method...")
try:
    from py_clob_client.client import ClobClient
    # 尝试获取源码
    source = inspect.getsource(ClobClient.get_balance)
    print("get_balance source:")
    print(source[:2000])
except Exception as e:
    print(f"Cannot get source: {e}")

# 直接获取余额
print("\nDirect balance call:")
print(f"get_balance(): {client.get_balance()}")

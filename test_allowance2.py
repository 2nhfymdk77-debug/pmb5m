import sys
sys.path.insert(0, ".")
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds

private_key = '0x8820445ec69f6e4526081be00bfa5ee1c5c9d1cf21f3bbeecbe1ceeb18878d5e'
creds = ApiCreds(
    api_key='128dc9d1-de1f-2f81-6620-7b5e96124d05',
    api_secret='1MMjS33p39XeVZwgzo_5djrgeRErf2dglPTaM_DQ9OQ=',
    api_passphrase='10320352192155cc7f6ad32f63a99363a9fe4df13c4225a208ce1c8ef6bbe5ac'
)

client = ClobClient(
    host="https://clob.polymarket.com",
    chain_id=137,
    key=private_key,
    creds=creds,
    signature_type=2,  # GNOSIS_SAFE
    funder="0xBceC747B92F6da7d4469be15eaBEA927CB88E6Df"
)

print(f"Client created, address: {client.get_address()}")

# 检查授权状态
print("\nChecking allowance...")
try:
    allowance = client.get_balance_allowance()
    print(f"get_balance_allowance(): {allowance}")
except Exception as e:
    print(f"get_balance_allowance() error: {e}")

# 更新授权
print("\nUpdating allowance...")
try:
    result = client.update_balance_allowance()
    print(f"update_balance_allowance(): {result}")
except Exception as e:
    print(f"update_balance_allowance() error: {e}")

# 再次检查授权
print("\nChecking allowance after update...")
try:
    allowance = client.get_balance_allowance()
    print(f"get_balance_allowance(): {allowance}")
except Exception as e:
    print(f"get_balance_allowance() error: {e}")

# 检查余额
print("\nChecking balance...")
try:
    balance = client.get_balance()
    print(f"get_balance(): {balance}")
except Exception as e:
    print(f"get_balance() error: {e}")

"""
诊断脚本：检查 Polymarket 订单簿结构
"""
import requests
import json

# CLOB API 基础 URL
CLOB_BASE = "https://clob.polymarket.com"

# 测试 token IDs（从日志中获取）
YES_TOKEN_ID = "91279360140366781909926259262770302989730117214413252952794386750514291113107"
NO_TOKEN_ID = "37743657735270845169141448760593690652020988362926829995749767772042395199569"

print("=" * 60)
print("Polymarket 订单簿诊断")
print("=" * 60)

# 获取 YES 订单簿
print(f"\n[YES 订单簿]")
print(f"Token ID: {YES_TOKEN_ID[:20]}...")
yes_url = f"{CLOB_BASE}/book?token_id={YES_TOKEN_ID}"
print(f"URL: {yes_url}")

yes_resp = requests.get(yes_url, timeout=5)
if yes_resp.status_code == 200:
    yes_book = yes_resp.json()
    
    print(f"\n订单簿结构:")
    print(f"  keys: {yes_book.keys()}")
    
    asks = yes_book.get("asks", [])
    bids = yes_book.get("bids", [])
    
    print(f"\n  Asks (卖单): {len(asks)} 个")
    if asks:
        print(f"  前3个卖单:")
        for i, ask in enumerate(asks[:3]):
            print(f"    {i+1}. price={ask.get('price')}, size={ask.get('size')}")
    
    print(f"\n  Bids (买单): {len(bids)} 个")
    if bids:
        print(f"  前3个买单:")
        for i, bid in enumerate(bids[:3]):
            print(f"    {i+1}. price={bid.get('price')}, size={bid.get('size')}")
    
    # 计算中间价
    if asks and bids:
        best_ask = float(asks[0].get("price", 0))
        best_bid = float(bids[0].get("price", 0))
        mid_price = (best_ask + best_bid) / 2
        print(f"\n  价格分析:")
        print(f"    最低卖价 (ask): {best_ask:.4f}")
        print(f"    最高买价 (bid): {best_bid:.4f}")
        print(f"    中间价 (mid): {mid_price:.4f}")
        print(f"    价差 (spread): {best_ask - best_bid:.4f}")
else:
    print(f"请求失败: {yes_resp.status_code}")

# 获取 NO 订单簿
print(f"\n{'=' * 60}")
print(f"[NO 订单簿]")
print(f"Token ID: {NO_TOKEN_ID[:20]}...")
no_url = f"{CLOB_BASE}/book?token_id={NO_TOKEN_ID}"
print(f"URL: {no_url}")

no_resp = requests.get(no_url, timeout=5)
if no_resp.status_code == 200:
    no_book = no_resp.json()
    
    print(f"\n订单簿结构:")
    print(f"  keys: {no_book.keys()}")
    
    asks = no_book.get("asks", [])
    bids = no_book.get("bids", [])
    
    print(f"\n  Asks (卖单): {len(asks)} 个")
    if asks:
        print(f"  前3个卖单:")
        for i, ask in enumerate(asks[:3]):
            print(f"    {i+1}. price={ask.get('price')}, size={ask.get('size')}")
    
    print(f"\n  Bids (买单): {len(bids)} 个")
    if bids:
        print(f"  前3个买单:")
        for i, bid in enumerate(bids[:3]):
            print(f"    {i+1}. price={bid.get('price')}, size={bid.get('size')}")
    
    # 计算中间价
    if asks and bids:
        best_ask = float(asks[0].get("price", 0))
        best_bid = float(bids[0].get("price", 0))
        mid_price = (best_ask + best_bid) / 2
        print(f"\n  价格分析:")
        print(f"    最低卖价 (ask): {best_ask:.4f}")
        print(f"    最高买价 (bid): {best_bid:.4f}")
        print(f"    中间价 (mid): {mid_price:.4f}")
        print(f"    价差 (spread): {best_ask - best_bid:.4f}")
else:
    print(f"请求失败: {no_resp.status_code}")

# 总结
print(f"\n{'=' * 60}")
print("总结:")
print("=" * 60)
print("\n如果 YES + NO 的价格总和接近 2.00，说明:")
print("  - 这是两个独立的订单簿")
print("  - 应该使用中间价（mid price）而不是最低卖价")
print("\n正确的价格表示:")
print("  - YES 价格 = YES 中间价")
print("  - NO 价格 = NO 中间价")
print("  - YES + NO 应该接近 1.00（考虑价差可能略大于1）")

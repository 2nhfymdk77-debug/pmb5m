"""
现代化界面显示模块
提供桌面应用程序级别的视觉效果
"""
import os
import sys
import time
import hashlib
import json
from typing import Dict, List, Any, Optional
from datetime import datetime


class Colors:
    """ANSI 颜色代码"""

    # 基础颜色
    BLACK = '\033[30m'
    RED = '\033[31m'
    GREEN = '\033[32m'
    YELLOW = '\033[33m'
    BLUE = '\033[34m'
    MAGENTA = '\033[35m'
    CYAN = '\033[36m'
    WHITE = '\033[37m'

    # 亮色
    BRIGHT_BLACK = '\033[90m'
    BRIGHT_RED = '\033[91m'
    BRIGHT_GREEN = '\033[92m'
    BRIGHT_YELLOW = '\033[93m'
    BRIGHT_BLUE = '\033[94m'
    BRIGHT_MAGENTA = '\033[95m'
    BRIGHT_CYAN = '\033[96m'
    BRIGHT_WHITE = '\033[97m'

    # 背景颜色
    BG_BLACK = '\033[40m'
    BG_RED = '\033[41m'
    BG_GREEN = '\033[42m'
    BG_YELLOW = '\033[43m'
    BG_BLUE = '\033[44m'
    BG_MAGENTA = '\033[45m'
    BG_CYAN = '\033[46m'
    BG_WHITE = '\033[47m'

    # 样式
    RESET = '\033[0m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    ITALIC = '\033[3m'
    UNDERLINE = '\033[4m'
    BLINK = '\033[5m'
    REVERSE = '\033[7m'
    HIDDEN = '\033[8m'
    STRIKETHROUGH = '\033[9m'

    # 特殊颜色组合
    SUCCESS = GREEN
    WARNING = YELLOW
    ERROR = RED
    INFO = CYAN
    HIGHLIGHT = BRIGHT_YELLOW
    MUTED = BRIGHT_BLACK


class BoxStyle:
    """边框样式"""

    # 单线边框
    SINGLE = {
        'tl': '┌', 'tr': '┐', 'bl': '└', 'br': '┘',
        'h': '─', 'v': '│', 'c': '┼'
    }

    # 双线边框
    DOUBLE = {
        'tl': '╔', 'tr': '╗', 'bl': '╚', 'br': '╝',
        'h': '═', 'v': '║', 'c': '╬'
    }

    # 圆角边框
    ROUNDED = {
        'tl': '╭', 'tr': '╮', 'bl': '╰', 'br': '╯',
        'h': '─', 'v': '│', 'c': '┼'
    }

    # 粗边框
    HEAVY = {
        'tl': '┏', 'tr': '┓', 'bl': '┗', 'br': '┛',
        'h': '━', 'v': '┃', 'c': '╋'
    }


class Icons:
    """图标"""

    # 状态图标
    SUCCESS = '['
    ERROR = '[X]'
    WARNING = '⚠'
    INFO = 'ℹ'
    BULLET = '•'

    # 箭头
    UP = '↑'
    DOWN = '↓'
    LEFT = '←'
    RIGHT = '→'
    UP_RIGHT = '↗'
    DOWN_RIGHT = '↘'

    # 货币
    DOLLAR = '$'
    EURO = '€'
    BITCOIN = '₿'

    # 图形
    BAR_FULL = '█'
    BAR_PARTIAL = '▓'
    BAR_EMPTY = '░'

    # 表情
    HAPPY = '😊'
    SAD = '😢'
    THUMBS_UP = '👍'
    THUMBS_DOWN = '👎'
    ROCKET = '🚀'
    CHART = '[UP]'
    MONEY = '[$$]'
    TREND_UP = '[UP]'
    TREND_DOWN = '[DN]'
    LOCK = '🔒'
    UNLOCK = '🔓'
    BELL = '🔔'
    STAR = '⭐'


class ModernFormatter:
    """现代化格式化工具"""

    @staticmethod
    def color(text: str, color_code: str) -> str:
        """给文本添加颜色"""
        return f"{color_code}{text}{Colors.RESET}"

    @staticmethod
    def bold(text: str) -> str:
        """加粗文本"""
        return f"{Colors.BOLD}{text}{Colors.RESET}"

    @staticmethod
    def dim(text: str) -> str:
        """淡化文本"""
        return f"{Colors.DIM}{text}{Colors.RESET}"

    @staticmethod
    def underline(text: str) -> str:
        """下划线文本"""
        return f"{Colors.UNDERLINE}{text}{Colors.RESET}"

    @staticmethod
    def highlight(text: str) -> str:
        """高亮文本"""
        return f"{Colors.BOLD}{Colors.HIGHLIGHT}{text}{Colors.RESET}"

    @staticmethod
    def success(text: str) -> str:
        """成功文本"""
        return f"{Colors.SUCCESS}{text}{Colors.RESET}"

    @staticmethod
    def error(text: str) -> str:
        """错误文本"""
        return f"{Colors.ERROR}{text}{Colors.RESET}"

    @staticmethod
    def warning(text: str) -> str:
        """警告文本"""
        return f"{Colors.WARNING}{text}{Colors.RESET}"

    @staticmethod
    def info(text: str) -> str:
        """信息文本"""
        return f"{Colors.INFO}{text}{Colors.RESET}"

    @staticmethod
    def format_price(price: float) -> str:
        """格式化价格（自动检测格式）
        
        如果输入 > 1，当作美分处理（除以100）
        如果输入 <= 1，当作小数处理（直接显示）
        """
        if price > 1:
            # 美分格式，如 75 -> $0.75
            return f"${price / 100:.2f}"
        else:
            # 小数格式，如 0.75 -> $0.75
            return f"${price:.2f}"

    @staticmethod
    def format_balance(balance_usd: float) -> str:
        """格式化余额（美元，不转换）"""
        return f"${balance_usd:.2f}"

    @staticmethod
    def format_percentage(value: float) -> str:
        """格式化百分比"""
        return f"{value:.2f}%"

    @staticmethod
    def format_number(number: float, decimals: int = 2) -> str:
        """格式化数字"""
        return f"{number:.{decimals}f}"

    @staticmethod
    def format_duration(seconds: float) -> str:
        """格式化持续时间"""
        if seconds < 1:
            return f"{seconds * 1000:.0f}ms"
        elif seconds < 60:
            return f"{seconds:.1f}s"
        else:
            minutes = int(seconds // 60)
            secs = int(seconds % 60)
            return f"{minutes}m {secs}s"


class Box:
    """边框框"""

    def __init__(self, style: str = 'DOUBLE', width: int = 80):
        self.style = style
        self.width = width
        self.chars = getattr(BoxStyle, style, BoxStyle.DOUBLE)

    def draw_horizontal(self, content: Optional[str] = None) -> str:
        """绘制水平线"""
        if content:
            content_len = len(content)
            padding = max(0, self.width - content_len - 2)
            left_pad = padding // 2
            right_pad = padding - left_pad
            return f"{self.chars['h'] * left_pad} {content} {self.chars['h'] * right_pad}"
        else:
            return self.chars['h'] * self.width

    def draw_top(self, title: Optional[str] = None) -> str:
        """绘制顶部边框"""
        return f"{self.chars['tl']}{self.draw_horizontal(title)}{self.chars['tr']}"

    def draw_bottom(self) -> str:
        """绘制底部边框"""
        return f"{self.chars['bl']}{self.draw_horizontal()}{self.chars['br']}"

    def draw_line(self, text: str, padding: int = 1) -> str:
        """绘制一行"""
        content = f"{' ' * padding}{text}{' ' * padding}"
        content = content[:self.width - 2]  # 截断过长的内容
        content = content.ljust(self.width - 2)  # 填充空白
        return f"{self.chars['v']}{content}{self.chars['v']}"

    def wrap(self, lines: List[str]) -> str:
        """包裹内容"""
        result = [self.draw_top()]
        for line in lines:
            result.append(self.draw_line(line))
        result.append(self.draw_bottom())
        return '\n'.join(result)


class ProgressBar:
    """进度条"""

    def __init__(self, width: int = 40):
        self.width = width

    def render(self, progress: float, color: str = Colors.BRIGHT_GREEN) -> str:
        """
        渲染进度条

        Args:
            progress: 进度值 (0.0 - 1.0)
            color: 进度条颜色
        """
        filled = int(self.width * progress)
        bar = (
            f"{color}{Icons.BAR_FULL * filled}{Colors.RESET}"
            f"{Colors.DIM}{Icons.BAR_EMPTY * (self.width - filled)}{Colors.RESET}"
        )
        percentage = f"{progress * 100:.1f}%"
        return f"[{bar}] {percentage}"

    def render_with_value(self, current: int, total: int, color: str = Colors.BRIGHT_GREEN) -> str:
        """渲染带数值的进度条"""
        if total == 0:
            progress = 1.0
        else:
            progress = current / total
        bar = self.render(progress, color)
        return f"{bar} ({current}/{total})"


class Table:
    """表格"""

    def __init__(self, headers: List[str], widths: Optional[List[int]] = None):
        self.headers = headers
        self.widths = widths or [len(h) for h in headers]
        self.rows = []

    def add_row(self, row: List[str]) -> None:
        """添加行"""
        self.rows.append(row)

    def render(self, style: str = 'DEFAULT') -> str:
        """渲染表格"""
        result = []

        # 根据样式选择分隔符
        if style == 'DEFAULT':
            h_sep = '┌' + '┬'.join('─' * w for w in self.widths) + '┐'
            r_sep = '├' + '┼'.join('─' * w for w in self.widths) + '┤'
            b_sep = '└' + '┴'.join('─' * w for w in self.widths) + '┘'
            v_sep = '│'
        elif style == 'DOUBLE':
            h_sep = '╔' + '╦'.join('═' * w for w in self.widths) + '╗'
            r_sep = '╠' + '╬'.join('═' * w for w in self.widths) + '╣'
            b_sep = '╚' + '╩'.join('═' * w for w in self.widths) + '╝'
            v_sep = '║'
        else:
            h_sep = '┌' + '┬'.join('─' * w for w in self.widths) + '┐'
            r_sep = '├' + '┼'.join('─' * w for w in self.widths) + '┤'
            b_sep = '└' + '┴'.join('─' * w for w in self.widths) + '┘'
            v_sep = '│'

        # 渲染顶部边框
        result.append(h_sep)

        # 渲染表头
        header_row = []
        for i, header in enumerate(self.headers):
            header_row.append(header.center(self.widths[i]))
        result.append(v_sep + v_sep.join(header_row) + v_sep)

        # 渲染分隔线
        result.append(r_sep)

        # 渲染数据行
        for row in self.rows:
            data_row = []
            for i, cell in enumerate(row):
                data_row.append(cell.ljust(self.widths[i]))
            result.append(v_sep + v_sep.join(data_row) + v_sep)

        # 渲染底部边框
        result.append(b_sep)

        return '\n'.join(result)


class Card:
    """卡片组件"""

    def __init__(self, title: str, width: int = 40, style: str = 'DOUBLE'):
        self.title = title
        self.width = width
        self.style = style
        self.box = Box(style, width)
        self.formatter = ModernFormatter()
        self.content = []

    def add_line(self, text: str, color: str = '') -> None:
        """添加一行内容"""
        if color:
            text = self.formatter.color(text, color)
        self.content.append(text)

    def add_spacer(self) -> None:
        """添加空行"""
        self.content.append('')

    def render(self) -> str:
        """渲染卡片"""
        return self.box.wrap(self.content)


class TradingDashboard:
    """现代化交易仪表盘"""

    def __init__(self):
        self.formatter = ModernFormatter()
        self.box_style = 'DOUBLE'

    def print_header(self) -> str:
        """打印标题"""
        lines = [
            self.formatter.bold(self.formatter.color("═══════════════════════════════════════════════════════════════════════════════", Colors.CYAN)),
            self.formatter.bold(self.formatter.color("  POLYMARKET 自动交易系统 v2.0", Colors.BRIGHT_CYAN)),
            self.formatter.bold(self.formatter.color("  Advanced Trading Dashboard", Colors.DIM)),
            self.formatter.bold(self.formatter.color("═══════════════════════════════════════════════════════════════════════════════", Colors.CYAN)),
        ]
        return '\n'.join(lines)

    def print_system_status(self, mode: str, api_status: str, market_id: str, is_running: bool) -> str:
        """打印系统状态"""
        card = Card(f"{Icons.BELL} 系统状态", 80, self.box_style)

        # 运行模式
        mode_text = {
            "simulation_real_data": f"{Icons.ROCKET} 模拟交易（使用真实数据）",
            "simulation_mock_data": f"{Icons.ROCKET} 模拟交易（使用模拟数据）",
            "real": f"{Icons.MONEY} 真实交易",
        }.get(mode, mode)

        mode_color = Colors.BRIGHT_GREEN if is_running else Colors.YELLOW
        card.add_line(f"运行模式: {self.formatter.bold(self.formatter.color(mode_text, mode_color))}")

        # API 状态
        api_color = {
            "connected": Colors.BRIGHT_GREEN,
            "disconnected": Colors.RED,
            "error": Colors.RED,
        }.get(api_status, Colors.YELLOW)

        api_text = {
            "connected": f"{Icons.SUCCESS} 已连接",
            "disconnected": f"{Icons.ERROR} 未连接",
            "error": f"{Icons.WARNING} 错误",
        }.get(api_status, api_status)

        card.add_line(f"API 状态: {self.formatter.color(api_text, api_color)}")

        # 市场ID
        if market_id:
            card.add_line(f"市场ID:   {self.formatter.dim(market_id[:40] + '...')}")

        return card.render()

    def print_market_info(self, market_data: Dict[str, Any], update_time: str, update_duration: float) -> str:
        """打印市场信息"""
        card = Card(f"{Icons.CHART} 市场信息", 80, self.box_style)

        # YES 和 NO 价格
        yes_price = market_data.get('yes_price', 0)
        no_price = market_data.get('no_price', 0)
        best_bid = market_data.get('best_bid', 0)
        best_ask = market_data.get('best_ask', 0)
        spread = market_data.get('spread', 0)

        card.add_line(f"YES 价格: {self.formatter.bold(self.formatter.format_price(yes_price))} {self.formatter.color(Icons.TREND_UP if yes_price > 50 else Icons.TREND_DOWN, Colors.GREEN if yes_price > 50 else Colors.RED)}")
        card.add_line(f"NO  价格: {self.formatter.bold(self.formatter.format_price(no_price))} {self.formatter.color(Icons.TREND_DOWN if no_price > 50 else Icons.TREND_UP, Colors.GREEN if no_price > 50 else Colors.RED)}")

        card.add_spacer()

        # 买卖价差
        spread_color = Colors.GREEN if spread < 5 else Colors.YELLOW if spread < 10 else Colors.RED
        card.add_line(f"买一价:   {self.formatter.format_price(best_bid)}")
        card.add_line(f"卖一价:   {self.formatter.format_price(best_ask)}")
        card.add_line(f"价差:     {self.formatter.color(f'{spread} 美分', spread_color)}")

        card.add_spacer()

        # 数据更新
        card.add_line(f"更新时间: {self.formatter.dim(update_time)}")
        duration_color = Colors.GREEN if update_duration < 500 else Colors.YELLOW if update_duration < 1000 else Colors.RED
        card.add_line(f"更新耗时: {self.formatter.color(f'{update_duration:.0f}ms', duration_color)}")

        return card.render()

    def print_account_info(self, balance: float, initial_balance: float, leverage: int) -> str:
        """打印账户信息"""
        card = Card(f"{Icons.DOLLAR} 账户信息", 80, self.box_style)

        profit = balance - initial_balance
        profit_percentage = (profit / initial_balance * 100) if initial_balance > 0 else 0

        # 盈亏颜色
        if profit > 0:
            profit_color = Colors.BRIGHT_GREEN
            profit_icon = Icons.TREND_UP
        elif profit < 0:
            profit_color = Colors.RED
            profit_icon = Icons.TREND_DOWN
        else:
            profit_color = Colors.YELLOW
            profit_icon = Icons.BULLET

        card.add_line(f"初始余额: {self.formatter.format_balance(initial_balance)}")
        card.add_line(f"当前余额: {self.formatter.bold(self.formatter.format_balance(balance))}")

        card.add_spacer()

        # 盈亏显示
        profit_text = f"{self.formatter.color(profit_icon, profit_color)} {self.formatter.color(f'{self.formatter.format_balance(profit)} ({self.formatter.format_percentage(profit_percentage)})', profit_color)}"
        card.add_line(f"累计盈亏: {profit_text}")

        card.add_spacer()

        # 杠杆
        card.add_line(f"当前杠杆: {self.formatter.bold(f'{leverage}x')}")

        return card.render()

    def print_trading_stats(self, stats: Dict[str, Any]) -> str:
        """打印交易统计"""
        card = Card(f"{Icons.STAR} 交易统计", 80, self.box_style)

        total_trades = stats.get('total_trades', 0)
        win_trades = stats.get('win_trades', 0)
        loss_trades = stats.get('loss_trades', 0)
        total_profit = stats.get('total_profit', 0)
        win_rate = stats.get('win_rate', 0)

        # 胜率颜色
        if win_rate > 50:
            win_rate_color = Colors.BRIGHT_GREEN
        elif win_rate > 40:
            win_rate_color = Colors.YELLOW
        else:
            win_rate_color = Colors.RED

        card.add_line(f"总交易次数: {self.formatter.bold(str(total_trades))}")
        card.add_line(f"盈利次数:   {self.formatter.color(str(win_trades), Colors.BRIGHT_GREEN)}")
        card.add_line(f"亏损次数:   {self.formatter.color(str(loss_trades), Colors.RED)}")

        card.add_spacer()

        # 胜率
        card.add_line(f"胜率:       {self.formatter.color(f'{self.formatter.format_percentage(win_rate)}', win_rate_color)}")

        card.add_spacer()

        # 总盈亏
        if total_profit > 0:
            total_profit_color = Colors.BRIGHT_GREEN
            total_profit_icon = Icons.TREND_UP
        elif total_profit < 0:
            total_profit_color = Colors.RED
            total_profit_icon = Icons.TREND_DOWN
        else:
            total_profit_color = Colors.YELLOW
            total_profit_icon = Icons.BULLET

        card.add_line(f"总盈亏:     {self.formatter.color(total_profit_icon, total_profit_color)} {self.formatter.color(f'{self.formatter.format_price(total_profit)}', total_profit_color)}")

        return card.render()

    def print_current_position(self, position: Dict[str, Any]) -> str:
        """打印当前持仓"""
        card = Card(f"{Icons.LOCK} 当前持仓", 80, self.box_style)

        if not position:
            card.add_line(self.formatter.dim("当前无持仓"))
            return card.render()

        position_type = position.get('type', 'N/A')
        token = position.get('token', 'UNKNOWN')
        entry_price = position.get('entry_price', 0)
        size = position.get('size', 0)
        pnl = position.get('pnl', 0)

        # 持仓类型
        type_color = Colors.BRIGHT_GREEN if position_type == 'LONG' else Colors.RED
        card.add_line(f"持仓代币: {self.formatter.bold(self.formatter.color(f'{token} ({position_type})', type_color))}")

        card.add_spacer()

        # 持仓信息
        card.add_line(f"开仓价格: {self.formatter.format_price(entry_price)}")
        card.add_line(f"持仓数量: {self.formatter.bold(f'{size}')}")
        card.add_line(f"当前盈亏: {self.formatter.format_price(pnl)}")

        # 盈亏状态
        if pnl > 0:
            pnl_status = f"{Icons.SUCCESS} 盈利"
            pnl_color = Colors.BRIGHT_GREEN
        elif pnl < 0:
            pnl_status = f"{Icons.ERROR} 亏损"
            pnl_color = Colors.RED
        else:
            pnl_status = f"{Icons.BULLET} 平衡"
            pnl_color = Colors.YELLOW

        card.add_line(f"状态:     {self.formatter.color(pnl_status, pnl_color)}")

        return card.render()

    def print_trading_params(self, params: Dict[str, Any]) -> str:
        """打印交易参数"""
        card = Card(f"⚙️  交易参数", 80, self.box_style)

        entry_price = params.get('entry_price', 0)
        stop_loss = params.get('stop_loss', 0)
        take_profit = params.get('take_profit', 0)
        trade_cycle_minutes = params.get('trade_cycle_minutes', 5)

        card.add_line(f"开仓价格:   {self.formatter.bold(self.formatter.format_price(entry_price))}")
        card.add_line(f"止损价格:   {self.formatter.color(self.formatter.format_price(stop_loss), Colors.RED)}")
        card.add_line(f"止盈价格:   {self.formatter.color(self.formatter.format_price(take_profit), Colors.BRIGHT_GREEN)}")
        card.add_line(f"交易周期:   {self.formatter.bold(f'{trade_cycle_minutes}')} 分钟")
        card.add_line(f"仓位规则:   余额≥初始×3^n → 开仓=2^n")

        return card.render()

    def print_pending_orders(self, orders: Dict[str, Any]) -> str:
        """打印挂单状态"""
        card = Card(f"📋 挂单状态", 80, self.box_style)

        if not orders:
            card.add_line(self.formatter.dim("当前无挂单"))
            return card.render()

        card.add_line(f"挂单数量: {self.formatter.bold(str(len(orders)))}")

        card.add_spacer()

        for order_id, order in orders.items():
            token = order.get('token', 'N/A')
            side = order.get('side', 'N/A')
            price = order.get('price', 0)
            size = order.get('size', 0)

            side_color = Colors.BRIGHT_GREEN if side.upper() == 'BUY' else Colors.RED
            side_icon = Icons.TREND_UP if side.upper() == 'BUY' else Icons.TREND_DOWN

            card.add_line(f"{self.formatter.color(f'[{token}]', side_color)} {self.formatter.color(side_icon, side_color)} {self.formatter.format_price(price)} × {size}")

        return card.render()

    def print_stop_take_orders(self, stop_loss_order: Optional[Dict], take_profit_order: Optional[Dict]) -> str:
        """打印止损止盈订单状态"""
        card = Card(f"[TGT] 止损止盈订单", 80, self.box_style)

        if not stop_loss_order and not take_profit_order:
            card.add_line(self.formatter.dim("当前无止损止盈订单"))
            return card.render()

        if stop_loss_order:
            card.add_line(f"止损订单: [OK] 已设置")
        else:
            card.add_line(f"止损订单: {self.formatter.dim('未设置')}")

        if take_profit_order:
            card.add_line(f"止盈订单: [OK] 已设置")
        else:
            card.add_line(f"止盈订单: {self.formatter.dim('未设置')}")

        return card.render()

    def print_trade_history(self, trades: List[Any], limit: int = 5) -> str:
        """打印交易历史"""
        card = Card(f"📜 交易历史 (最近 {limit} 笔)", 80, self.box_style)

        if not trades:
            card.add_line(self.formatter.dim("暂无交易记录"))
            return card.render()

        # 显示最近的交易
        recent_trades = trades[-limit:]

        # 创建表格
        table = Table(
            ['时间', '类型', '开仓', '平仓', '盈亏', '原因'],
            [16, 8, 8, 8, 10, 12]
        )

        for trade in recent_trades:
            # 兼容字典和 dataclass
            if isinstance(trade, dict):
                timestamp = trade.get('timestamp', 'N/A')
                trade_type = trade.get('type', 'N/A')
                entry_price = trade.get('entry_price', 0)
                exit_price = trade.get('exit_price', 0)
                pnl = trade.get('pnl', 0)
                exit_reason = trade.get('exit_reason', 'N/A')
                token = trade.get('token', 'UNKNOWN')
            else:
                timestamp = getattr(trade, 'timestamp', 'N/A')
                trade_type = getattr(trade, 'type', 'N/A')
                entry_price = getattr(trade, 'entry_price', 0)
                exit_price = getattr(trade, 'exit_price', 0)
                pnl = getattr(trade, 'pnl', 0)
                exit_reason = getattr(trade, 'exit_reason', 'N/A')
                token = getattr(trade, 'token', 'UNKNOWN')

            # 格式化时间
            try:
                dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                time_str = dt.strftime('%H:%M:%S')
            except:
                time_str = timestamp[:8]

            # 格式化盈亏
            pnl_str = self.formatter.format_price(pnl)
            if pnl > 0:
                pnl_str = f"+{pnl_str}"
                pnl_color = Colors.BRIGHT_GREEN
            elif pnl < 0:
                pnl_color = Colors.RED
            else:
                pnl_color = Colors.YELLOW

            # 类型显示
            type_str = f"{token[:3]}"
            type_color = Colors.BRIGHT_GREEN if trade_type == 'LONG' else Colors.RED

            table.add_row([
                self.formatter.dim(time_str),
                self.formatter.color(type_str, type_color),
                self.formatter.format_price(entry_price),
                self.formatter.format_price(exit_price),
                self.formatter.color(pnl_str, pnl_color),
                self.formatter.dim(exit_reason[:10])
            ])

        return card.render()

    def print_alert(self, message: str, alert_type: str = "info") -> str:
        """打印警告信息"""
        icons = {
            "info": Icons.INFO,
            "success": Icons.SUCCESS,
            "warning": Icons.WARNING,
            "error": Icons.ERROR,
        }
        colors = {
            "info": Colors.BRIGHT_CYAN,
            "success": Colors.BRIGHT_GREEN,
            "warning": Colors.BRIGHT_YELLOW,
            "error": Colors.BRIGHT_RED,
        }

        icon = icons.get(alert_type, Icons.INFO)
        color = colors.get(alert_type, Colors.BRIGHT_CYAN)

        return f"\n{self.formatter.color(f'{icon} {message}', color)}\n"

    def clear_screen(self):
        """清屏"""
        os.system('cls' if os.name == 'nt' else 'clear')


class RealTimeDisplay:
    """实时显示控制器"""

    def __init__(self, refresh_interval: int = 5):
        """
        初始化实时显示控制器
        
        Args:
            refresh_interval: 完整界面刷新间隔（秒），避免频繁清屏导致闪烁
        """
        self.dashboard = TradingDashboard()
        self.refresh_interval = refresh_interval
        self._last_full_refresh = 0
        self._last_data_hash = None

    def show_full_dashboard(
        self,
        market_data: Dict[str, Any],
        balance: float,
        initial_balance: float,
        leverage: int,
        stats: Dict[str, Any],
        position: Dict[str, Any],
        orders: Dict[str, Any],
        trades: List[Dict[str, Any]],
        params: Dict[str, Any],
        mode: str,
        api_status: str,
        market_id: str,
        update_time: str,
        update_duration: float,
        is_running: bool = True,
        stop_loss_order: Optional[Dict] = None,
        take_profit_order: Optional[Dict] = None,
        force_refresh: bool = False,
    ):
        """显示完整仪表盘
        
        Args:
            force_refresh: 强制刷新完整界面（不清屏）
        """
        import hashlib
        import json
        
        current_time = time.time()
        
        # 计算当前数据哈希，用于检测数据变化
        data_hash = hashlib.md5(json.dumps({
            'balance': balance,
            'market_data': market_data,
            'position': position,
            'orders': orders,
            'api_status': api_status,
            'stop_loss_order': stop_loss_order,
            'take_profit_order': take_profit_order,
        }, sort_keys=True).encode()).hexdigest()
        
        # 智能刷新策略：只有当数据变化或超过刷新间隔时才完整刷新
        should_full_refresh = (
            force_refresh or 
            self._last_data_hash != data_hash or
            current_time - self._last_full_refresh >= self.refresh_interval
        )
        
        self._last_data_hash = data_hash
        
        if should_full_refresh:
            # 清屏
            self.dashboard.clear_screen()
            self._last_full_refresh = current_time
            
            # 打印主标题
            print(self.dashboard.print_header())

            # 两列布局
            left_column = []
            right_column = []

            # 系统状态 (全宽)
            system_status = self.dashboard.print_system_status(mode, api_status, market_id, is_running)
            print(system_status)
            print()

            # 市场信息 (左侧)
            left_column.append(self.dashboard.print_market_info(market_data, update_time, update_duration))

            # 账户信息 (右侧)
            right_column.append(self.dashboard.print_account_info(balance, initial_balance, leverage))

            # 当前持仓 (左侧)
            left_column.append(self.dashboard.print_current_position(position))

            # 止损止盈订单 (右侧，替代挂单状态)
            right_column.append(self.dashboard.print_stop_take_orders(stop_loss_order, take_profit_order))

            # 交易统计 (左侧)
            left_column.append(self.dashboard.print_trading_stats(stats))

            # 交易参数 (右侧)
            right_column.append(self.dashboard.print_trading_params(params))

            # 打印两列布局
            max_lines = max(len(left_column) if isinstance(left_column[0], str) else 1,
                           len(right_column) if isinstance(right_column[0], str) else 1)

            for i in range(max_lines):
                left_part = left_column[i] if i < len(left_column) else ''
                right_part = right_column[i] if i < len(right_column) else ''

                if left_part and right_part:
                    # 两列都有内容，需要并排显示
                    left_lines = left_part.split('\n')
                    right_lines = right_part.split('\n')

                    max_sublines = max(len(left_lines), len(right_lines))
                    for j in range(max_sublines):
                        left_line = left_lines[j] if j < len(left_lines) else ' ' * 80
                        right_line = right_lines[j] if j < len(right_lines) else ' ' * 80
                        print(left_line[:80] + '  ' + right_line[:80])
                    print()
                elif left_part:
                    print(left_part)
                    print()
                elif right_part:
                    print(right_part)
                    print()

            # 交易历史 (全宽)
            print(self.dashboard.print_trade_history(trades, limit=3))

            # 打印提示信息
            print(self.dashboard.print_alert("提示: 按 Ctrl+C 停止交易", "info"))

"""
Polymarket 自动交易 - Windows桌面应用
Excel风格GUI界面

运行方式：
  python app_gui.py

打包为exe：
  pyinstaller --onefile --windowed --name "PolymarketTrader" app_gui.py
"""
import sys
import threading
from datetime import datetime
from typing import Optional, Dict, Any
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QComboBox, QPushButton, QTableWidget, 
    QTableWidgetItem, QHeaderView, QGroupBox, QSplitter,
    QTextEdit, QMessageBox, QSpinBox, QDoubleSpinBox, QFrame
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QObject
from PyQt5.QtGui import QFont, QColor, QBrush

from config import TradingConfig
from trading_engine_unified import UnifiedTrader


class SignalEmitter(QObject):
    """信号发射器（用于线程间通信）"""
    status_signal = pyqtSignal(str, float, float, float, float, float)
    trade_signal = pyqtSignal(dict)
    log_signal = pyqtSignal(str)


class ExcelStyleSpinBox(QDoubleSpinBox):
    """Excel风格数字输入框"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("""
            QDoubleSpinBox {
                border: 1px solid #ccc;
                background: white;
                padding: 2px;
                font-size: 12px;
            }
            QDoubleSpinBox:focus {
                border: 2px solid #0078d4;
            }
        """)


class ExcelStyleLineEdit(QLineEdit):
    """Excel风格文本框"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("""
            QLineEdit {
                border: 1px solid #ccc;
                background: white;
                padding: 2px;
                font-size: 12px;
            }
            QLineEdit:focus {
                border: 2px solid #0078d4;
            }
        """)


class ExcelStyleComboBox(QComboBox):
    """Excel风格下拉框"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("""
            QComboBox {
                border: 1px solid #ccc;
                background: white;
                padding: 2px;
                font-size: 12px;
            }
            QComboBox:focus {
                border: 2px solid #0078d4;
            }
            QComboBox::drop-down {
                border: none;
                width: 20px;
            }
        """)


class ExcelStyleTable(QTableWidget):
    """Excel风格表格"""
    def __init__(self, rows: int, cols: int, parent=None):
        super().__init__(rows, cols, parent)
        self.setStyleSheet("""
            QTableWidget {
                border: 1px solid #ccc;
                background: white;
                gridline-color: #e0e0e0;
                font-size: 12px;
            }
            QTableWidget::item {
                padding: 2px;
            }
            QTableWidget::item:selected {
                background: #0078d4;
                color: white;
            }
            QHeaderView::section {
                background: #f5f5f5;
                border: 1px solid #ccc;
                padding: 4px;
                font-weight: bold;
            }
        """)
        self.setAlternatingRowColors(True)
        self.horizontalHeader().setHighlightSections(False)
        self.verticalHeader().setVisible(False)


class MainWindow(QMainWindow):
    """主窗口"""
    
    def __init__(self):
        super().__init__()
        
        self.trader: Optional[UnifiedTrader] = None
        self.trading_thread: Optional[threading.Thread] = None
        self.signals = SignalEmitter()
        
        # 连接信号
        self.signals.status_signal.connect(self._update_status)
        self.signals.trade_signal.connect(self._add_trade_record)
        self.signals.log_signal.connect(self._add_log)
        
        self._init_ui()
        self._load_config()
        
        # 定时器更新时间
        self.timer = QTimer()
        self.timer.timeout.connect(self._update_time)
        self.timer.start(1000)
        
        # 自动启动交易（延迟500ms，等待窗口完全加载）
        QTimer.singleShot(500, self._auto_start_trading)
    
    def _init_ui(self):
        """初始化界面"""
        self.setWindowTitle("Polymarket 自动交易系统 - V4策略")
        self.setGeometry(80, 50, 1400, 900)  # 更大的窗口
        
        # 主布局
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(8)
        main_layout.setContentsMargins(15, 15, 15, 15)
        
        # 顶部：标题栏
        title_frame = QFrame()
        title_frame.setStyleSheet("background: #0078d4; padding: 5px;")
        title_layout = QHBoxLayout(title_frame)
        title_label = QLabel("Polymarket 自动交易系统 - V4策略（自动运行）")
        title_label.setStyleSheet("color: white; font-size: 18px; font-weight: bold;")
        title_layout.addWidget(title_label)
        title_layout.addStretch()
        
        self.time_label = QLabel("")
        self.time_label.setStyleSheet("color: white; font-size: 12px;")
        title_layout.addWidget(self.time_label)
        
        main_layout.addWidget(title_frame)
        
        # 中间：交易区域（直接显示，无配置面板）
        trade_widget = self._create_trade_panel()
        main_layout.addWidget(trade_widget)
        
        # 设置样式
        self.setStyleSheet("""
            QMainWindow {
                background: #f0f0f0;
            }
            QGroupBox {
                font-weight: bold;
                border: 1px solid #ccc;
                border-radius: 3px;
                margin-top: 10px;
                padding-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
            }
            QPushButton {
                background: #0078d4;
                color: white;
                border: none;
                padding: 12px 24px;
                font-size: 14px;
                font-weight: bold;
                border-radius: 5px;
                min-width: 120px;
            }
            QPushButton:hover {
                background: #106ebe;
            }
            QPushButton:pressed {
                background: #005a9e;
            }
            QPushButton:disabled {
                background: #a0a0a0;
            }
        """)
    
    def _create_trade_panel(self) -> QWidget:
        """创建交易面板"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(6)
        
        # 状态区域（紧凑）
        status_group = QGroupBox("实时状态")
        status_group.setStyleSheet("""
            QGroupBox {
                font-weight: bold;
                font-size: 12px;
                margin-top: 8px;
                padding-top: 5px;
            }
        """)
        status_layout = QHBoxLayout(status_group)
        status_layout.setContentsMargins(5, 2, 5, 2)
        status_layout.setSpacing(5)
        
        self.status_labels = {}
        status_items = [
            ("状态", "等待启动"),
            ("YES价格", "0%"),
            ("NO价格", "0%"),
            ("剩余时间", "0:00"),
            ("余额", "$0.00"),
            ("总盈亏", "$0.00"),
        ]
        
        for name, value in status_items:
            frame = QFrame()
            frame.setStyleSheet("""
                QFrame {
                    background: white;
                    border: 1px solid #ccc;
                    border-radius: 3px;
                }
            """)
            frame_layout = QVBoxLayout(frame)
            frame_layout.setContentsMargins(6, 2, 6, 2)
            frame_layout.setSpacing(1)
            
            name_label = QLabel(name)
            name_label.setStyleSheet("color: #666; font-size: 9px;")
            frame_layout.addWidget(name_label)
            
            value_label = QLabel(value)
            value_label.setStyleSheet("font-size: 14px; font-weight: bold;")
            frame_layout.addWidget(value_label)
            
            self.status_labels[name] = value_label
            status_layout.addWidget(frame)
        
        layout.addWidget(status_group, 0)  # 比例0，使用最小高度
        
        # 交易历史表格（主要显示区域）
        history_group = QGroupBox("交易历史")
        history_group.setStyleSheet("""
            QGroupBox {
                font-weight: bold;
                font-size: 16px;
                border: 2px solid #0078d4;
                border-radius: 5px;
                margin-top: 12px;
                padding-top: 8px;
                background: #f8f9fa;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 20px;
                padding: 0 10px;
                color: #0078d4;
                font-size: 16px;
            }
        """)
        history_layout = QVBoxLayout(history_group)
        history_layout.setContentsMargins(8, 8, 8, 8)
        
        self.history_table = ExcelStyleTable(0, 7)
        self.history_table.setStyleSheet("""
            QTableWidget {
                border: 1px solid #ccc;
                background: white;
                gridline-color: #d0d0d0;
                font-size: 15px;
                font-weight: 500;
            }
            QTableWidget::item {
                padding: 8px;
                height: 32px;
            }
            QTableWidget::item:selected {
                background: #0078d4;
                color: white;
            }
            QHeaderView::section {
                background: #0078d4;
                color: white;
                border: none;
                padding: 10px;
                font-weight: bold;
                font-size: 14px;
            }
        """)
        self.history_table.setHorizontalHeaderLabels([
            "时间", "代币", "方向", "开仓价", "平仓价", "盈亏", "原因"
        ])
        self.history_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.history_table.setRowCount(50)
        self.history_table.verticalHeader().setDefaultSectionSize(36)  # 行高36px
        
        history_layout.addWidget(self.history_table)
        layout.addWidget(history_group, 6)  # 比例6，主要区域
        
        # 日志区域（次要显示）
        log_group = QGroupBox("运行日志")
        log_group.setStyleSheet("""
            QGroupBox {
                font-weight: bold;
                font-size: 12px;
                margin-top: 8px;
                padding-top: 5px;
            }
        """)
        log_layout = QVBoxLayout(log_group)
        log_layout.setContentsMargins(5, 5, 5, 5)
        
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setStyleSheet("""
            QTextEdit {
                background: #1e1e1e;
                color: #d4d4d4;
                font-family: Consolas, Monaco, monospace;
                font-size: 11px;
                border: 1px solid #ccc;
                border-radius: 3px;
            }
        """)
        log_layout.addWidget(self.log_text)
        
        layout.addWidget(log_group, 3)  # 比例3，次要区域
        
        # 底部：按钮区域（简化版）
        btn_layout = QHBoxLayout()
        
        self.stop_btn = QPushButton("⏹ 停止交易")
        self.stop_btn.clicked.connect(self._stop_trading)
        self.stop_btn.setEnabled(False)  # 初始禁用，自动启动后启用
        btn_layout.addWidget(self.stop_btn)
        
        btn_layout.addStretch()
        layout.addLayout(btn_layout)
        
        return widget
    
    def _check_api_keys(self) -> bool:
        """检查API密钥是否配置"""
        try:
            config = TradingConfig.load()
            return bool(config.private_key and config.api_key and config.api_secret)
        except:
            return False
    
    def _load_config(self):
        """加载配置"""
        try:
            config = TradingConfig.load()
            # 加载到界面
            self._add_log("配置已加载")
        except Exception as e:
            self._add_log(f"加载配置失败: {e}")
    
    def _save_config(self):
        """保存配置"""
        self._add_log("配置已保存")
    
    def _auto_start_trading(self):
        """自动启动交易（使用V4策略默认参数）"""
        try:
            config = TradingConfig.load()
            
            # V4策略默认参数
            mode = "CONTINUOUS"  # V4策略模式
            entry_price = 70.0   # 买入价格 70%
            stop_loss = 45.0     # 止损价格 45%
            take_profit = 95.0   # 止盈价格 95%
            buy_limit = 85.0     # 买入限制 85%
            last_min_stop = 0.0  # 最后1分钟止损（0=使用固定止损）
            last_min_profit = 0.0  # 最后1分钟止盈（0=不止盈）
            
            # 仓位控制参数
            position_divisor = 12.0  # 基础仓位比例
            position_multiplier_threshold = 3.0  # 倍增阈值
            max_position_multiplier = 8.0  # 最大倍数
            min_position_amount = 1.0  # 最小开仓金额
            
            self._add_log("=" * 60)
            self._add_log("自动启动V4策略交易")
            self._add_log("=" * 60)
            self._add_log(f"策略模式: {mode}")
            self._add_log(f"买入价格: {entry_price}%")
            self._add_log(f"止损价格: {stop_loss}%")
            self._add_log(f"止盈价格: {take_profit}%")
            self._add_log(f"买入限制: {buy_limit}%")
            
            # 创建交易引擎
            self.trader = UnifiedTrader(
                config=config,
                mode=mode,
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                buy_limit=buy_limit,
                last_minute_stop_loss=last_min_stop if last_min_stop > 0 else None,
                last_minute_take_profit=last_min_profit if last_min_profit > 0 else None,
                position_divisor=position_divisor,
                position_multiplier_threshold=position_multiplier_threshold,
                max_position_multiplier=max_position_multiplier,
                min_position_amount=min_position_amount,
                on_status_update=self._on_status_update,
                on_trade_update=self._on_trade_update,
                on_log=self._on_log,
            )
            
            # 启动交易线程
            self.trading_thread = threading.Thread(target=self.trader.start, daemon=True)
            self.trading_thread.start()
            
            # 更新按钮状态
            self.stop_btn.setEnabled(True)
            
            # 立即更新状态显示
            self.status_labels["状态"].setText("启动中...")
            self.status_labels["YES价格"].setText("获取中...")
            self.status_labels["NO价格"].setText("获取中...")
            
        except Exception as e:
            self._add_log(f"自动启动失败: {e}")
            QMessageBox.critical(self, "错误", f"自动启动失败: {e}")
    
    def _stop_trading(self):
        """停止交易"""
        if self.trader:
            self.trader.stop()
        
        self.stop_btn.setEnabled(False)
        
        self._add_log("交易已停止")
    
    def _on_status_update(self, status: str, yes_price: float, no_price: float, 
                          remaining: float, balance: float, pnl: float):
        """状态更新回调（线程中调用）"""
        self.signals.status_signal.emit(status, yes_price, no_price, remaining, balance, pnl)
    
    def _on_trade_update(self, trade: dict):
        """交易更新回调（线程中调用）"""
        self.signals.trade_signal.emit(trade)
    
    def _on_log(self, message: str):
        """日志回调（线程中调用）"""
        self.signals.log_signal.emit(message)
    
    def _update_status(self, status: str, yes_price: float, no_price: float,
                       remaining: float, balance: float, pnl: float):
        """更新状态（主线程）"""
        # 更新状态
        if "状态" in self.status_labels:
            self.status_labels["状态"].setText(status)
        
        # 更新价格（yes_price已经是百分比形式，如70.5表示70.5%）
        if "YES价格" in self.status_labels:
            self.status_labels["YES价格"].setText(f"{yes_price:.1f}%")
        if "NO价格" in self.status_labels:
            self.status_labels["NO价格"].setText(f"{no_price:.1f}%")
        
        # 更新剩余时间
        if "剩余时间" in self.status_labels:
            mins = int(remaining // 60)
            secs = int(remaining % 60)
            self.status_labels["剩余时间"].setText(f"{mins}:{secs:02d}")
        
        # 更新余额
        if "余额" in self.status_labels:
            self.status_labels["余额"].setText(f"${balance:.2f}")
        
        # 更新盈亏
        if "总盈亏" in self.status_labels:
            pnl_text = f"{'+' if pnl >= 0 else ''}${pnl:.2f}"
            self.status_labels["总盈亏"].setText(pnl_text)
            if pnl >= 0:
                self.status_labels["总盈亏"].setStyleSheet("font-size: 14px; font-weight: bold; color: green;")
            else:
                self.status_labels["总盈亏"].setStyleSheet("font-size: 14px; font-weight: bold; color: red;")
    
    def _add_trade_record(self, trade: dict):
        """添加交易记录（主线程）"""
        # 找到第一个空行
        row = 0
        while row < self.history_table.rowCount():
            if self.history_table.item(row, 0) is None:
                break
            row += 1
        
        if row >= self.history_table.rowCount():
            self.history_table.insertRow(0)
            row = 0
        else:
            # 将现有行下移
            for i in range(self.history_table.rowCount() - 1, row, -1):
                for j in range(7):
                    item = self.history_table.item(i - 1, j)
                    if item:
                        self.history_table.setItem(i, j, item.clone())
        
        # 插入新记录
        self.history_table.setItem(row, 0, QTableWidgetItem(trade.get("time", "")))
        self.history_table.setItem(row, 1, QTableWidgetItem(trade.get("token", "")))
        self.history_table.setItem(row, 2, QTableWidgetItem("买入"))
        self.history_table.setItem(row, 3, QTableWidgetItem(f"{trade.get('entry_price', 0)*100:.1f}%"))
        self.history_table.setItem(row, 4, QTableWidgetItem(f"{trade.get('exit_price', 0)*100:.1f}%"))
        
        pnl = trade.get("pnl", 0)
        pnl_item = QTableWidgetItem(f"{'+' if pnl >= 0 else ''}${pnl:.2f}")
        if pnl >= 0:
            pnl_item.setForeground(QBrush(QColor("green")))
        else:
            pnl_item.setForeground(QBrush(QColor("red")))
        self.history_table.setItem(row, 5, pnl_item)
        self.history_table.setItem(row, 6, QTableWidgetItem(trade.get("reason", "")))
    
    def _add_log(self, message: str):
        """添加日志（主线程）"""
        time_str = datetime.now().strftime("%H:%M:%S")
        self.log_text.append(f"[{time_str}] {message}")
    
    def _update_time(self):
        """更新时间显示"""
        self.time_label.setText(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    
    def closeEvent(self, event):
        """窗口关闭事件"""
        if self.trader and self.trader.is_running:
            reply = QMessageBox.question(
                self, "确认", "交易正在进行中，确定要退出吗？",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply == QMessageBox.No:
                event.ignore()
                return
            
            self.trader.stop()
        
        event.accept()


def main():
    """主函数"""
    app = QApplication(sys.argv)
    
    # 设置字体
    font = QFont("Microsoft YaHei", 10)
    app.setFont(font)
    
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()

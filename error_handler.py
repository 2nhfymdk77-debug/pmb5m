"""
错误处理模块
提供统一的错误处理和恢复机制
"""
import logging
from typing import Optional, Dict, Any
from enum import Enum


class ErrorCategory(Enum):
    """错误类别"""
    NETWORK = "network"  # 网络错误
    API = "api"  # API 错误
    AUTHENTICATION = "auth"  # 认证错误
    VALIDATION = "validation"  # 验证错误
    CONFIG = "config"  # 配置错误
    TRADING = "trading"  # 交易错误
    UNKNOWN = "unknown"  # 未知错误


class PolymarketError(Exception):
    """基础错误类"""

    def __init__(
        self,
        message: str,
        category: ErrorCategory = ErrorCategory.UNKNOWN,
        details: Optional[Dict[str, Any]] = None,
        original_exception: Optional[Exception] = None
    ):
        self.message = message
        self.category = category
        self.details = details or {}
        self.original_exception = original_exception
        super().__init__(self.message)

    def __str__(self):
        parts = [f"[{self.category.value.upper()}] {self.message}"]
        if self.details:
            parts.append(f"Details: {self.details}")
        if self.original_exception:
            parts.append(f"Original: {type(self.original_exception).__name__}: {str(self.original_exception)}")
        return "\n  ".join(parts)


class NetworkError(PolymarketError):
    """网络错误"""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None, original_exception: Optional[Exception] = None):
        super().__init__(message, ErrorCategory.NETWORK, details, original_exception)


class APIError(PolymarketError):
    """API 错误"""

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        details: Optional[Dict[str, Any]] = None,
        original_exception: Optional[Exception] = None
    ):
        if details is None:
            details = {}
        if status_code is not None:
            details["status_code"] = status_code
        super().__init__(message, ErrorCategory.API, details, original_exception)


class AuthenticationError(PolymarketError):
    """认证错误"""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None, original_exception: Optional[Exception] = None):
        super().__init__(message, ErrorCategory.AUTHENTICATION, details, original_exception)


class ValidationError(PolymarketError):
    """验证错误"""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None, original_exception: Optional[Exception] = None):
        super().__init__(message, ErrorCategory.VALIDATION, details, original_exception)


class ConfigError(PolymarketError):
    """配置错误"""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None, original_exception: Optional[Exception] = None):
        super().__init__(message, ErrorCategory.CONFIG, details, original_exception)


class TradingError(PolymarketError):
    """交易错误"""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None, original_exception: Optional[Exception] = None):
        super().__init__(message, ErrorCategory.TRADING, details, original_exception)


class ErrorHandler:
    """错误处理器（静默模式）"""

    def __init__(self, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger(__name__)
        # 静默模式：使用不输出堆栈的 handler
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s", datefmt="%H:%M:%S"))
            self.logger.addHandler(handler)
        self.logger.propagate = False  # 防止重复输出
        self.last_error_time: Dict[str, float] = {}
        self.min_interval = 30  # 30秒内同一错误不重复输出

    def handle(
        self,
        error: Exception,
        context: Optional[str] = None,
        recoverable: bool = True,
        should_raise: bool = False
    ) -> Optional[PolymarketError]:
        """处理错误（静默模式，不输出堆栈，30秒去重）"""
        # 转换为 PolymarketError
        polymarket_error = self._convert_error(error)
        
        # 生成错误键（用于去重）
        error_type = type(error).__name__
        error_key = f"{error_type}:{context or polymarket_error.message}"

        # 检查去重
        import time
        current_time = time.time()
        should_log = True
        
        if error_key in self.last_error_time:
            if current_time - self.last_error_time[error_key] < self.min_interval:
                should_log = False
        
        self.last_error_time[error_key] = current_time

        # 只输出简化的错误消息，不带堆栈
        if should_log:
            log_message = polymarket_error.message
            if context:
                log_message = f"{context}: {log_message}"
            self.logger.warning(log_message)

        if should_raise:
            raise polymarket_error from error

        return polymarket_error

    def _convert_error(self, error: Exception) -> PolymarketError:
        """将通用异常转换为 PolymarketError"""
        if isinstance(error, PolymarketError):
            return error

        # 根据异常类型分类
        error_str = str(error).lower()

        if "network" in error_str or "connection" in error_str or "timeout" in error_str:
            return NetworkError(str(error), original_exception=error)

        if "authentication" in error_str or "unauthorized" in error_str or "credentials" in error_str:
            return AuthenticationError(str(error), original_exception=error)

        if "validation" in error_str or "invalid" in error_str:
            return ValidationError(str(error), original_exception=error)

        if "config" in error_str:
            return ConfigError(str(error), original_exception=error)

        # 默认为 API 错误
        return APIError(str(error), original_exception=error)

    def get_error_summary(self) -> Dict[str, int]:
        """获取错误统计摘要"""
        return self.error_counts.copy()

    def reset_counts(self) -> None:
        """重置错误计数"""
        self.error_counts.clear()

    def log_summary(self) -> None:
        """记录错误统计摘要"""
        if not self.error_counts:
            self.logger.info("无错误记录")
            return

        self.logger.info("=" * 60)
        self.logger.info("错误统计摘要:")
        for error_type, count in self.error_counts.items():
            self.logger.info(f"  {error_type}: {count} 次")
        self.logger.info("=" * 60)


def safe_execute(
    func,
    error_handler: ErrorHandler,
    context: Optional[str] = None,
    default_return: Any = None,
    should_raise: bool = False
) -> Any:
    """
    安全执行函数，自动处理错误

    Args:
        func: 要执行的函数
        error_handler: 错误处理器
        context: 错误上下文
        default_return: 默认返回值
        should_raise: 是否抛出错误

    Returns:
        函数结果或默认返回值
    """
    try:
        return func()
    except Exception as e:
        error_handler.handle(e, context, should_raise=should_raise)
        return default_return

"""
Rate Limiter for API calls using Token Bucket algorithm
"""
import time
import asyncio
from typing import Optional


class RateLimiter:
    """
    Token Bucket 기반 Rate Limiter

    Usage:
        limiter = RateLimiter(rate=3.0)  # 3 req/sec
        await limiter.acquire()
    """

    def __init__(self, rate: float = 3.0):
        """
        Args:
            rate: 초당 허용 요청 수 (예: 3.0 = 3 req/sec)
        """
        self.rate = rate
        self.tokens = rate
        self.max_tokens = rate
        self.last_update = time.monotonic()
        self.lock = asyncio.Lock()

    async def acquire(self) -> None:
        """
        토큰을 소비하고 요청 실행 권한 획득
        토큰이 부족하면 대기
        """
        try:
            async with asyncio.timeout(5.0):  # 5초 타임아웃
                async with self.lock:
                    # 토큰 보충 (lock 획득 후 1회만 실행)
                    now = time.monotonic()
                    elapsed = now - self.last_update
                    self.tokens = min(
                        self.max_tokens,
                        self.tokens + elapsed * self.rate
                    )
                    self.last_update = now

                    # 토큰이 부족하면 대기
                    if self.tokens < 1:
                        sleep_time = (1 - self.tokens) / self.rate
                        await asyncio.sleep(sleep_time)
                        # 대기 후 토큰 재보충
                        now = time.monotonic()
                        elapsed = now - self.last_update
                        self.tokens = min(
                            self.max_tokens,
                            self.tokens + elapsed * self.rate
                        )
                        self.last_update = now

                    # 토큰 1개 소비
                    self.tokens -= 1
        except asyncio.TimeoutError:
            raise TimeoutError("Rate limiter lock acquisition timeout after 5 seconds")


class ExponentialBackoff:
    """
    Exponential Backoff 재시도 전략

    Usage:
        backoff = ExponentialBackoff()
        for attempt in range(max_retries):
            try:
                result = await api_call()
                break
            except Exception as e:
                await backoff.sleep(attempt)
    """

    def __init__(
        self,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        multiplier: float = 2.0
    ):
        """
        Args:
            base_delay: 초기 대기 시간 (초)
            max_delay: 최대 대기 시간 (초)
            multiplier: 지수 배율
        """
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.multiplier = multiplier

    async def sleep(self, attempt: int) -> None:
        """
        재시도 attempt에 따라 대기

        Args:
            attempt: 재시도 횟수 (0부터 시작)
        """
        delay = min(
            self.base_delay * (self.multiplier ** attempt),
            self.max_delay
        )
        await asyncio.sleep(delay)

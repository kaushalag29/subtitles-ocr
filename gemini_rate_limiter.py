"""
Gemini API Rate Limiter with Sliding Window and Exponential Backoff

This module provides a robust rate limiting solution for Google Gemini API calls
that prevents hitting rate limits proactively while handling errors gracefully.

Usage:
    from gemini_rate_limiter import GeminiRateLimiter
    
    limiter = GeminiRateLimiter()
    
    # Wrap your API calls
    response = limiter.generate_content(
        model=model,
        prompt="Your prompt here",
        model_name="gemini-2.5-flash"  # or "gemini-2.5-flash-lite"
    )
"""

import time
import logging
from collections import deque
from datetime import datetime, timedelta
import random
from typing import Optional, Callable, Any
import google.generativeai as genai
from google.api_core import exceptions as google_exceptions

logger = logging.getLogger(__name__)


class GeminiRateLimiter:
    """
    Rate limiter for Gemini API calls using sliding window algorithm.
    
    Implements:
    - Sliding window rate limiting (proactive)
    - Exponential backoff with jitter (reactive)
    - Model-specific rate limits
    - Thread-safe request tracking
    """
    
    # Rate limits for different models (Free Tier - RPM)
    MODEL_RATE_LIMITS = {
        'gemini-2.5-pro': 5,
        'gemini-2.5-flash': 10,
        'gemini-2.5-flash-preview': 10,
        'gemini-2.5-flash-lite': 15,
        'gemini-2.5-flash-lite-preview': 15,
        'gemini-2.5-flash-lite-preview-06-17': 15,
        'gemini-2.0-flash': 15,
        'gemini-2.0-flash-lite': 30,
        # Add more models as needed
    }
    
    # Default rate limit if model not found
    DEFAULT_RATE_LIMIT = 5  # Conservative default
    
    # Exponential backoff settings
    MAX_RETRIES = 5
    BASE_DELAY = 1.0  # Base delay in seconds
    MAX_DELAY = 60.0  # Maximum delay in seconds
    
    def __init__(self):
        """Initialize the rate limiter with sliding window trackers for each model."""
        # Dictionary to store request timestamps for each model
        # Key: model_name, Value: deque of timestamps
        self.request_history = {}
        
        # Dictionary to store last request time for minimum spacing
        self.last_request_time = {}
        
        logger.info("GeminiRateLimiter initialized with model-specific rate limits")
    
    def _get_rate_limit(self, model_name: str) -> int:
        """
        Get the rate limit (RPM) for a specific model.
        
        Args:
            model_name: Name of the Gemini model
            
        Returns:
            Rate limit in requests per minute
        """
        # Normalize model name (remove version suffixes for matching)
        normalized_name = model_name.lower()
        
        # Check for exact match first
        if normalized_name in self.MODEL_RATE_LIMITS:
            return self.MODEL_RATE_LIMITS[normalized_name]
        
        # Check for partial matches (e.g., "gemini-2.5-flash" in "gemini-2.5-flash-preview-12-17")
        for model_key, limit in self.MODEL_RATE_LIMITS.items():
            if model_key in normalized_name:
                return limit
        
        logger.warning(f"Model '{model_name}' not found in rate limits. Using conservative default: {self.DEFAULT_RATE_LIMIT} RPM")
        return self.DEFAULT_RATE_LIMIT
    
    def _clean_old_requests(self, model_name: str):
        """
        Remove request timestamps older than 60 seconds (sliding window).
        
        Args:
            model_name: Name of the Gemini model
        """
        if model_name not in self.request_history:
            self.request_history[model_name] = deque()
            return
        
        current_time = time.time()
        cutoff_time = current_time - 60  # 60 seconds window
        
        # Remove old timestamps
        while self.request_history[model_name] and self.request_history[model_name][0] < cutoff_time:
            self.request_history[model_name].popleft()
    
    def _wait_if_needed(self, model_name: str):
        """
        Wait if necessary to stay within rate limits (proactive rate limiting).
        
        Args:
            model_name: Name of the Gemini model
        """
        rate_limit = self._get_rate_limit(model_name)
        
        # Clean old requests first
        self._clean_old_requests(model_name)
        
        # Initialize if needed
        if model_name not in self.request_history:
            self.request_history[model_name] = deque()
        
        current_time = time.time()
        
        # Check if we're at the rate limit
        if len(self.request_history[model_name]) >= rate_limit:
            # Calculate how long to wait
            oldest_request = self.request_history[model_name][0]
            time_since_oldest = current_time - oldest_request
            wait_time = 60 - time_since_oldest
            
            if wait_time > 0:
                logger.info(f"Rate limit reached for {model_name} ({rate_limit} RPM). Waiting {wait_time:.2f}s...")
                time.sleep(wait_time)
                # Clean again after waiting
                self._clean_old_requests(model_name)
        
        # Add minimum spacing between requests (avoid bursts)
        # Distribute requests evenly: 60 seconds / rate_limit
        min_spacing = 60.0 / rate_limit
        
        if model_name in self.last_request_time:
            time_since_last = current_time - self.last_request_time[model_name]
            if time_since_last < min_spacing:
                spacing_wait = min_spacing - time_since_last
                logger.debug(f"Applying minimum spacing: waiting {spacing_wait:.2f}s")
                time.sleep(spacing_wait)
    
    def _record_request(self, model_name: str):
        """
        Record a request timestamp for rate tracking.
        
        Args:
            model_name: Name of the Gemini model
        """
        current_time = time.time()
        
        if model_name not in self.request_history:
            self.request_history[model_name] = deque()
        
        self.request_history[model_name].append(current_time)
        self.last_request_time[model_name] = current_time
    
    def _exponential_backoff_wait(self, attempt: int) -> float:
        """
        Calculate exponential backoff wait time with jitter.
        
        Args:
            attempt: Current retry attempt number (0-indexed)
            
        Returns:
            Wait time in seconds
        """
        # Exponential: base * 2^attempt
        wait_time = self.BASE_DELAY * (2 ** attempt)
        
        # Add jitter (randomness) to prevent thundering herd
        jitter = random.uniform(0, wait_time * 0.3)  # 0-30% jitter
        wait_time += jitter
        
        # Cap at maximum delay
        wait_time = min(wait_time, self.MAX_DELAY)
        
        return wait_time
    
    def generate_content(
        self,
        model: genai.GenerativeModel,
        prompt: str,
        model_name: str,
        max_retries: Optional[int] = None,
        **kwargs
    ) -> Any:
        """
        Generate content with rate limiting and exponential backoff.
        
        Args:
            model: Initialized GenerativeModel instance
            prompt: The prompt to send to the model
            model_name: Name of the model (for rate limit tracking)
            max_retries: Maximum number of retries (uses class default if None)
            **kwargs: Additional arguments to pass to generate_content()
            
        Returns:
            Response from the model
            
        Raises:
            Exception: If all retries are exhausted
        """
        if max_retries is None:
            max_retries = self.MAX_RETRIES
        
        last_exception = None
        
        for attempt in range(max_retries):
            try:
                # Proactive rate limiting (wait if needed before making request)
                self._wait_if_needed(model_name)
                
                # Make the API call
                logger.debug(f"Making Gemini API call (attempt {attempt + 1}/{max_retries}) for model: {model_name}")
                response = model.generate_content(prompt, **kwargs)
                
                # Record successful request
                self._record_request(model_name)
                
                return response
                
            except google_exceptions.ResourceExhausted as e:
                # Rate limit hit (429 error)
                last_exception = e
                logger.warning(f"Rate limit error on attempt {attempt + 1}/{max_retries}: {e}")
                
                if attempt < max_retries - 1:
                    wait_time = self._exponential_backoff_wait(attempt)
                    logger.info(f"Backing off for {wait_time:.2f}s before retry...")
                    time.sleep(wait_time)
                else:
                    logger.error(f"Max retries ({max_retries}) exhausted for rate limit errors")
                    raise
            
            except google_exceptions.ServiceUnavailable as e:
                # Service temporarily unavailable (503)
                last_exception = e
                logger.warning(f"Service unavailable on attempt {attempt + 1}/{max_retries}: {e}")
                
                if attempt < max_retries - 1:
                    wait_time = self._exponential_backoff_wait(attempt)
                    logger.info(f"Backing off for {wait_time:.2f}s before retry...")
                    time.sleep(wait_time)
                else:
                    logger.error(f"Max retries ({max_retries}) exhausted for service unavailable errors")
                    raise
            
            except Exception as e:
                # Other errors - don't retry
                logger.error(f"Non-retryable error occurred: {type(e).__name__}: {e}")
                raise
        
        # If we get here, all retries failed
        if last_exception:
            raise last_exception
        else:
            raise Exception("Unknown error: all retries exhausted")
    
    def get_stats(self, model_name: Optional[str] = None) -> dict:
        """
        Get rate limiter statistics.
        
        Args:
            model_name: Specific model to get stats for (None for all models)
            
        Returns:
            Dictionary with statistics
        """
        if model_name:
            self._clean_old_requests(model_name)
            requests_in_window = len(self.request_history.get(model_name, []))
            rate_limit = self._get_rate_limit(model_name)
            
            return {
                'model': model_name,
                'requests_in_last_minute': requests_in_window,
                'rate_limit_rpm': rate_limit,
                'utilization_percent': (requests_in_window / rate_limit * 100) if rate_limit > 0 else 0
            }
        else:
            stats = {}
            for model in self.request_history.keys():
                stats[model] = self.get_stats(model)
            return stats


# Singleton instance for easy access
_global_limiter = None


def get_global_limiter() -> GeminiRateLimiter:
    """
    Get or create the global rate limiter instance.
    
    Returns:
        Global GeminiRateLimiter instance
    """
    global _global_limiter
    if _global_limiter is None:
        _global_limiter = GeminiRateLimiter()
    return _global_limiter


def reset_global_limiter():
    """Reset the global rate limiter (useful for testing)."""
    global _global_limiter
    _global_limiter = None


# Convenience function for direct usage
def rate_limited_generate(
    model: genai.GenerativeModel,
    prompt: str,
    model_name: str,
    **kwargs
) -> Any:
    """
    Convenience function for rate-limited content generation.
    
    Args:
        model: Initialized GenerativeModel instance
        prompt: The prompt to send to the model
        model_name: Name of the model
        **kwargs: Additional arguments
        
    Returns:
        Response from the model
    """
    limiter = get_global_limiter()
    return limiter.generate_content(model, prompt, model_name, **kwargs)

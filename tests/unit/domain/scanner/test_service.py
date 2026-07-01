"""
Unit tests for app/domain/scanner/service.py
"""

import asyncio

import pytest


@pytest.mark.asyncio
async def test_scan_token_exists():
    """Test that scan_token function exists in scanner service."""
    from app.domain.scanner import service

    assert hasattr(service, 'scan_token')
    assert callable(service.scan_token)


@pytest.mark.asyncio
async def test_scan_token_signature():
    """Test scan_token function signature."""
    import inspect

    from app.domain.scanner import service

    sig = inspect.signature(service.scan_token)
    params = list(sig.parameters.keys())

    assert 'token_address' in params
    assert 'chain' in params


@pytest.mark.asyncio
async def test_scan_token_defaults():
    """Test scan_token has proper defaults."""
    import inspect

    from app.domain.scanner import service

    sig = inspect.signature(service.scan_token)

    assert sig.parameters['chain'].default == 'ethereum'
    assert sig.parameters['tiers'].default is None
    assert sig.parameters['include_market_data'].default is True
    assert sig.parameters['include_social'].default is True


@pytest.mark.asyncio
async def test_scan_token_is_async():
    """Test scan_token is an async function."""
    from app.domain.scanner import service

    # Check it's a coroutine function
    assert asyncio.iscoroutinefunction(service.scan_token)

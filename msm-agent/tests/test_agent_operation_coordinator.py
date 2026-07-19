from __future__ import annotations

import asyncio
import threading

import pytest

from services.agent_operation_coordinator import (
    InvalidServerOperation,
    is_operation_active,
    operation,
    operation_async,
    reset_operation_coordinator_for_tests,
    server_id_from_container_name,
)


@pytest.fixture(autouse=True)
def _reset_coordinator():
    reset_operation_coordinator_for_tests()
    yield
    reset_operation_coordinator_for_tests()


def test_same_server_operations_are_serialized_across_threads() -> None:
    entered = threading.Event()
    release = threading.Event()
    second_entered = threading.Event()

    def first() -> None:
        with operation(42):
            entered.set()
            release.wait(2)

    def second() -> None:
        entered.wait(2)
        with operation(42):
            second_entered.set()

    first_thread = threading.Thread(target=first)
    second_thread = threading.Thread(target=second)
    first_thread.start()
    second_thread.start()
    assert entered.wait(1)
    assert is_operation_active(42) is True
    assert second_entered.wait(0.1) is False
    release.set()
    first_thread.join(2)
    second_thread.join(2)
    assert second_entered.is_set()


def test_different_servers_can_run_concurrently() -> None:
    entered = threading.Event()

    def second_server() -> None:
        with operation(2):
            entered.set()

    with operation(1):
        thread = threading.Thread(target=second_server)
        thread.start()
        assert entered.wait(1)
        thread.join(1)


def test_nested_same_context_does_not_deadlock_and_exception_releases() -> None:
    with pytest.raises(RuntimeError):
        with operation(7):
            with operation(7):
                raise RuntimeError("synthetic")
    assert is_operation_active(7) is False


def test_async_waiter_is_serialized_without_blocking_event_loop() -> None:
    async def scenario() -> list[str]:
        order: list[str] = []
        first_entered = asyncio.Event()
        release = asyncio.Event()

        async def first() -> None:
            async with operation_async(9):
                order.append("first")
                first_entered.set()
                await release.wait()

        async def second() -> None:
            await first_entered.wait()
            async with operation_async(9):
                order.append("second")

        tasks = [asyncio.create_task(first()), asyncio.create_task(second())]
        await first_entered.wait()
        await asyncio.sleep(0.05)
        assert order == ["first"]
        release.set()
        await asyncio.gather(*tasks)
        return order

    assert asyncio.run(scenario()) == ["first", "second"]


def test_container_name_and_server_id_validation() -> None:
    assert server_id_from_container_name("msm-srv-42", "msm-srv-") == 42
    for value in ("other-42", "msm-srv-", "msm-srv-0", "msm-srv-../1"):
        with pytest.raises(InvalidServerOperation):
            server_id_from_container_name(value, "msm-srv-")

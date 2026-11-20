import pytest
import os
from pathlib import Path
from src.tools.kernel import PythonKernelManager


@pytest.mark.asyncio
async def test_kernel_manager_lifecycle_and_state():
    km = PythonKernelManager(timeout_seconds=5.0)
    await km.start()
    assert km.is_running is True

    # 1. State persistence across turns
    res1 = await km.execute("test_var = 100\nprint('VAR SET')")
    assert res1["status"] == "ok"
    assert "VAR SET" in res1["stdout"]

    res2 = await km.execute("print('CALCULATED:', test_var * 3)")
    assert res2["status"] == "ok"
    assert "CALCULATED: 300" in res2["stdout"]

    await km.stop()
    assert km.is_running is False


@pytest.mark.asyncio
async def test_kernel_manager_chart_artifact_capture():
    km = PythonKernelManager(timeout_seconds=10.0)
    await km.start()

    plot_code = """
years = [2020, 2021, 2022, 2023]
rev = [2.8, 3.0, 3.3, 3.5]
plt.figure(figsize=(7, 4))
plt.plot(years, rev, marker='o', label='Revenue ($B)')
plt.title('Test Revenue Trajectory')
save_chart('pytest_revenue')
"""
    res = await km.execute(plot_code)
    assert res["status"] == "ok"
    assert len(res["artifacts"]) >= 1
    chart_rel_path = res["artifacts"][0]
    assert chart_rel_path.startswith("vault/charts/pytest_revenue")
    assert chart_rel_path.endswith(".png")

    full_path = Path(chart_rel_path)
    assert full_path.exists()
    assert full_path.stat().st_size > 1000  # Valid PNG file

    await km.stop()


@pytest.mark.asyncio
async def test_kernel_manager_timeout_interrupt():
    km = PythonKernelManager(timeout_seconds=1.5)
    await km.start()

    await km.execute("kept_var = 999")

    # Run loop exceeding timeout
    timeout_code = """
import time
time.sleep(10)
print('SHOULD NOT PRINT')
"""
    res = await km.execute(timeout_code)
    assert res["status"] == "timeout"
    assert "Execution Timeout" in res["stderr"]

    # Verify namespace is STILL ALIVE
    res_verify = await km.execute("print('PERSISTED:', kept_var)")
    assert res_verify["status"] == "ok"
    assert "PERSISTED: 999" in res_verify["stdout"]

    await km.stop()

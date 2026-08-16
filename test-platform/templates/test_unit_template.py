"""
单元测试模板
命名约定: test_<被测模块>.py，放在 tests/unit/ 下
- 只测一个函数/类，不跨文件
- DB/Redis 用 fixture，真实依赖都 Mock
- 断言优先："正向 + 异常 + 边界" 三条腿
"""
import pytest


class TestXxx:
    """被测对象: Xxx"""

    # --- 正向 ---
    async def test_xxx_happy_path(self):
        # Arrange
        data = {"key": "val"}
        # Act
        result = lambda d: d  # TODO: 替换成真实函数
        out = result(data)
        # Assert
        assert out == data

    # --- 异常分支 ---
    async def test_xxx_invalid_input_raises(self):
        with pytest.raises(ValueError):
            raise ValueError("示例异常")  # TODO: 真实调用

    # --- 边界 ---
    @pytest.mark.parametrize("val,expected", [
        (0, 0),
        (-1, 0),
        (10**9, 10**9),
    ])
    async def test_xxx_boundary(self, val, expected):
        assert max(0, val) == expected

import pytest

from tools.ai_model_advisor.feedback import UsageRecord


def _record(**overrides):
    values = {
        "provider": "openai",
        "model_id": "gpt-5.6-terra",
        "effort": "medium",
        "execution_mode": "single",
        "outcome": "success",
        "input_tokens": 100,
        "output_tokens": 20,
    }
    values.update(overrides)
    return UsageRecord(**values)


def test_cache_telemetry_can_partition_all_input_tokens():
    record = _record(cached_tokens=70, cache_creation_tokens=30)
    assert record.cached_tokens == 70
    assert record.cache_creation_tokens == 30


@pytest.mark.parametrize("field", ["cached_tokens", "cache_creation_tokens"])
def test_cache_telemetry_cannot_individually_exceed_input_tokens(field):
    with pytest.raises(ValueError, match=f"{field} must be <= input_tokens"):
        _record(**{field: 101})


def test_combined_cache_telemetry_cannot_exceed_input_tokens():
    with pytest.raises(
        ValueError,
        match=r"cached_tokens \+ cache_creation_tokens must be <= input_tokens",
    ):
        _record(cached_tokens=70, cache_creation_tokens=31)


def test_cache_telemetry_without_input_total_remains_accepted():
    record = _record(input_tokens=None, cached_tokens=25, cache_creation_tokens=5)
    assert record.cached_tokens == 25
    assert record.cache_creation_tokens == 5

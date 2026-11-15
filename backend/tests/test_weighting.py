from __future__ import annotations

import numpy as np
import pytest

from app.services.dsp.weighting import a_weight_db, c_weight_db, weight_gain


def test_a_weighting_reference_points() -> None:
    assert a_weight_db(1000.0) == pytest.approx(0.0, abs=0.05)
    # IEC 61672 典型值
    assert a_weight_db(63.0) == pytest.approx(-26.2, abs=0.5)
    assert a_weight_db(125.0) == pytest.approx(-16.1, abs=0.5)
    assert a_weight_db(4000.0) == pytest.approx(1.0, abs=0.5)


def test_c_weighting_reference_points() -> None:
    assert c_weight_db(1000.0) == pytest.approx(0.0, abs=0.05)
    assert c_weight_db(63.0) == pytest.approx(-0.8, abs=0.5)
    assert c_weight_db(4000.0) == pytest.approx(-0.8, abs=0.5)


def test_weight_gain_is_power_ratio() -> None:
    gain = weight_gain(np.array([1000.0]), "A")
    assert float(gain[0]) == pytest.approx(1.0, abs=1e-3)


def test_unknown_weighting_rejected() -> None:
    with pytest.raises(ValueError):
        weight_gain(np.array([1000.0]), "B")

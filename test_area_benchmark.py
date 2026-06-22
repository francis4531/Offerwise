"""Area benchmark tests (risk_check_engine) — network-free via mocks.

Strategy: mock every check_* so each sampled cell scores a fixed exposure
($20k = grade B via a CA fault-zone hit), then vary the target's own exposure
to land each of the three cases (typical / worse / better)."""
import pytest
import risk_check_engine as r


@pytest.fixture(autouse=True)
def _clear_cell_cache():
    r._cell_cache.clear()
    yield
    r._cell_cache.clear()


def _mock_neighbors(monkeypatch):
    """Every sampled cell scores exactly $20,000 (grade B)."""
    monkeypatch.setattr(r, 'check_fema_flood', lambda lat, lng: None)
    monkeypatch.setattr(r, 'check_earthquakes', lambda lat, lng: None)
    monkeypatch.setattr(r, 'check_epa_environmental', lambda lat, lng: None)
    monkeypatch.setattr(r, 'check_disaster_history', lambda s, c: None)
    monkeypatch.setattr(r, 'check_radon_zone', lambda c, s: None)
    assert r.RISK_COSTS['fault_zone_high'] == 20000  # guard: keeps the math honest
    monkeypatch.setattr(r, 'check_california_hazards',
                        lambda lat, lng, state: {'wildfire': None,
                                                 'fault_zone': {'level': 'HIGH', 'detail': 'x'}})


# --- grade ladder: single source of truth ---
@pytest.mark.parametrize("total,grade", [
    (0, 'A'), (1, 'B+'), (9999, 'B+'), (10000, 'B'), (24999, 'B'),
    (25000, 'C'), (40000, 'D'), (59999, 'D'), (60000, 'F'), (89000, 'F')])
def test_grade_ladder(total, grade):
    assert r._grade_for(total) == grade


def test_calculate_exposure_uses_shared_grade():
    exp = r.calculate_risk_exposure(
        flood=None, earthquakes=None, disasters=None,
        ca_hazards={'wildfire': None, 'fault_zone': {'level': 'HIGH', 'detail': 'x'}},
        air_quality=None, epa_environmental=None, radon=None)
    assert exp['total_exposure'] == 20000
    assert exp['grade'] == r._grade_for(20000) == 'B'


# --- sampling geometry ---
def test_sample_points_count_and_offset():
    pts = r._bench_sample_points(37.2, -121.8)
    assert len(pts) == 16
    assert all((round(p[0], 5), round(p[1], 5)) != (37.2, -121.8) for p in pts)


def test_cell_key_quantizes_to_grid():
    # points within the same ~2km cell share a key; far-apart points don't
    assert r._cell_key(37.234, -121.876) == r._cell_key(37.235, -121.877)
    assert r._cell_key(37.20, -121.80) != r._cell_key(37.30, -121.80)


# --- the three cases ---
def test_case_typical(monkeypatch):
    _mock_neighbors(monkeypatch)
    bm = r.run_area_benchmark(37.2, -121.8, 'CA', 'Santa Clara', target_exposure=20000)
    assert bm is not None
    assert bm['case'] == 'typical'
    assert bm['target_grade'] == 'B'
    assert bm['area_median_grade'] == 'B'
    assert bm['sample_n'] >= r.BENCH_MIN_SAMPLES
    assert bm['radius_miles'] == 0.9


def test_case_worse(monkeypatch):
    _mock_neighbors(monkeypatch)
    bm = r.run_area_benchmark(37.2, -121.8, 'CA', 'Santa Clara', target_exposure=89000)
    assert bm['case'] == 'worse'
    assert bm['target_grade'] == 'F'
    assert bm['area_median_grade'] == 'B'


def test_case_better(monkeypatch):
    _mock_neighbors(monkeypatch)
    bm = r.run_area_benchmark(37.2, -121.8, 'CA', 'Santa Clara', target_exposure=0)
    assert bm['case'] == 'better'
    assert bm['target_grade'] == 'A'


# --- graceful None (never break the core scan) ---
def test_none_when_all_cells_fail(monkeypatch):
    monkeypatch.setattr(r, 'check_disaster_history', lambda s, c: None)
    monkeypatch.setattr(r, 'check_radon_zone', lambda c, s: None)
    monkeypatch.setattr(r, '_score_cell',
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError('down')))
    bm = r.run_area_benchmark(37.2, -121.8, 'CA', 'Santa Clara', target_exposure=50000)
    assert bm is None


def test_none_when_shared_fetch_raises(monkeypatch):
    monkeypatch.setattr(r, 'check_disaster_history',
                        lambda s, c: (_ for _ in ()).throw(RuntimeError('x')))
    bm = r.run_area_benchmark(37.2, -121.8, 'CA', 'Santa Clara',
                              target_exposure=50000, shared_disasters=None)
    assert bm is None


# --- attach helper ---
def test_attach_benchmark_populates_result(monkeypatch):
    _mock_neighbors(monkeypatch)
    result = {'latitude': 37.2, 'longitude': -121.8, 'state': 'CA', 'county': 'Santa Clara',
              'risk_exposure': 89000, 'disaster_summary': None, 'radon': None}
    r._attach_benchmark(result)
    assert result['benchmark'] is not None
    assert result['benchmark']['case'] == 'worse'


def test_attach_benchmark_skips_error_result():
    result = {'error': 'bad address'}
    r._attach_benchmark(result)
    assert 'benchmark' not in result

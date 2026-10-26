from __future__ import annotations

from pmnode.contracts import SourceType
from pmnode.simulator import Scenario, SensorSimulator, SimulationConfig


def make_run(scenario=Scenario.HEALTHY, seed=42, duration=4.2, hz=50):
    return SensorSimulator(
        SimulationConfig(scenario=scenario, duration_s=duration, sample_hz=hz,
                         seed=seed, batch_duration_s=1.0),
        boot_id="fixed-boot",
    ).generate()


def flattened(run):
    return [sample for batch in run.batches for sample in batch.motion_samples]


def test_reproducible_same_seed():
    assert flattened(make_run(seed=8)) == flattened(make_run(seed=8))


def test_different_seeds_change_signal():
    assert flattened(make_run(seed=8)) != flattened(make_run(seed=9))


def test_no_dropout_sample_count():
    assert len(flattened(make_run(duration=2.0, hz=25))) == 50


def test_dht_interval_is_two_seconds():
    env = [sample for batch in make_run(duration=6.1).batches
           for sample in batch.environment_samples]
    assert [sample.t_mono_us for sample in env] == [0, 2_000_000, 4_000_000, 6_000_000]


def test_source_is_explicitly_simulated():
    assert all(batch.source_type is SourceType.SIMULATED for batch in make_run().batches)


def test_anomaly_injection_increases_peak():
    healthy = flattened(make_run())
    anomaly = flattened(make_run(Scenario.VIBRATION_ANOMALY))
    healthy_peak = max(abs(sample.ax_g) for sample in healthy[-50:])
    anomaly_peak = max(abs(sample.ax_g) for sample in anomaly[-50:])
    assert anomaly_peak > healthy_peak * 2


def test_transient_has_separate_label():
    run = make_run(Scenario.TRANSIENT_DISTURBANCE)
    assert run.labels[0]["label"] == "synthetic_transient"
    assert "label" not in type(run.batches[0]).model_fields


def test_dropout_has_timestamp_gaps_and_quality_counter():
    run = make_run(Scenario.SENSOR_DROPOUT)
    timestamps = [sample.t_mono_us for sample in flattened(run)]
    nominal = 1_000_000 // 50
    assert any(b - a > nominal for a, b in zip(timestamps, timestamps[1:]))
    assert run.batches[-1].quality.missed_deadlines > 0
    assert run.batches[-1].quality.dht_failures > 0


def test_ordering_across_batches():
    run = make_run()
    sequence = [sample.sample_seq for sample in flattened(run)]
    timestamps = [sample.t_mono_us for sample in flattened(run)]
    assert sequence == sorted(sequence) and len(sequence) == len(set(sequence))
    assert timestamps == sorted(timestamps) and len(timestamps) == len(set(timestamps))


def test_values_respect_configured_ranges():
    run = make_run(Scenario.VIBRATION_ANOMALY)
    assert all(max(abs(s.ax_g), abs(s.ay_g), abs(s.az_g)) < 4 for s in flattened(run))
    assert all(max(abs(s.gx_dps), abs(s.gy_dps), abs(s.gz_dps)) < 250
               for s in flattened(run))

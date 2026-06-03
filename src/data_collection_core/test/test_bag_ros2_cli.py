from data_collection_core.bag_ros2_cli import BagRos2CliBackend


class FakeProcess:
    def __init__(self):
        self.signals = []
        self.wait_timeouts = []
        self.returncode = None

    def send_signal(self, signal_number):
        self.signals.append(signal_number)

    def wait(self, timeout=None):
        self.wait_timeouts.append(timeout)
        self.returncode = 0
        return self.returncode

    def poll(self):
        return self.returncode


def test_start_launches_ros2_bag_record_with_topics_and_mcap_preset(monkeypatch, tmp_path):
    launched = {}
    fake_process = FakeProcess()

    def fake_popen(command):
        launched['command'] = command
        return fake_process

    monkeypatch.setattr('subprocess.Popen', fake_popen)

    backend = BagRos2CliBackend(
        topic_names=['/camera_head/color/image_raw/compressed', '/joint_states'],
        storage_id='mcap',
        storage_preset_profile='zstd_small',
    )

    backend.start(tmp_path / 'recording', topic_types={})

    assert launched['command'] == [
        'ros2',
        'bag',
        'record',
        '-s',
        'mcap',
        '--storage-preset-profile',
        'zstd_small',
        '/camera_head/color/image_raw/compressed',
        '/joint_states',
        '-o',
        str(tmp_path / 'recording'),
    ]
    assert backend.active


def test_stop_sends_sigint_and_waits_for_ros2_bag_to_flush(monkeypatch, tmp_path):
    fake_process = FakeProcess()
    monkeypatch.setattr('subprocess.Popen', lambda command: fake_process)

    backend = BagRos2CliBackend(
        topic_names=['/joint_states'],
        storage_id='mcap',
        storage_preset_profile='zstd_small',
        stop_timeout_sec=3.0,
    )
    backend.start(tmp_path / 'recording', topic_types={})

    backend.stop()

    assert fake_process.signals == [2]
    assert fake_process.wait_timeouts == [3.0]
    assert not backend.active


def test_write_serialized_is_noop_for_cli_backend():
    backend = BagRos2CliBackend(topic_names=['/joint_states'])

    backend.write_serialized('/joint_states', b'payload', 123)

    assert not backend.active


def test_record_command_omits_storage_preset_when_disabled(monkeypatch, tmp_path):
    launched = {}
    monkeypatch.setattr('subprocess.Popen', lambda command: launched.setdefault('command', command))

    backend = BagRos2CliBackend(
        topic_names=['/joint_states'],
        storage_id='mcap',
        storage_preset_profile='none',
    )

    backend.start(tmp_path / 'recording', topic_types={})

    assert launched['command'] == [
        'ros2',
        'bag',
        'record',
        '-s',
        'mcap',
        '/joint_states',
        '-o',
        str(tmp_path / 'recording'),
    ]

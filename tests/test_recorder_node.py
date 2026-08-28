from data_collection_recorder.recorder_node import McapRecorderNode
from data_collection_core.constants import DISCARD_LAST_EPISODE_CODE, FSM_HOLD_COMMAND
from data_collection_core.lerobot_conversion_config import LeRobotConversionConfig
from data_collection_core.lerobot_converter import LeRobotEpisodeConverter
from data_collection_core.episode_uploader import EpisodeUploader
from data_collection_core.upload_config import UploadConfig


class _Session:
    is_recording = False


class _Logger:
    def info(self, *_args, **_kwargs):
        pass

    def warn(self, *_args, **_kwargs):
        pass

    def error(self, *_args, **_kwargs):
        pass


def test_controller_stop_publishes_record_stop_even_when_not_recording():
    node = McapRecorderNode.__new__(McapRecorderNode)
    node.session = _Session()
    node.published_record_stop = False
    node._collector_warn = lambda *_args, **_kwargs: None
    node.get_logger = lambda: _Logger()
    node._publish_record_stop_signal = lambda: setattr(node, "published_record_stop", True)

    node._stop_recording(123, source="controller_14")

    assert node.published_record_stop is True


def test_record_stop_publish_failure_is_non_fatal():
    class _Publisher:
        def publish(self, _msg):
            raise RuntimeError("context is invalid")

    node = McapRecorderNode.__new__(McapRecorderNode)
    node._record_stop_publisher = _Publisher()
    node.get_logger = lambda: _Logger()

    node._publish_record_stop_signal()


def test_start_blocked_publishes_record_stop_and_robot_hold():
    class _Publisher:
        def __init__(self):
            self.messages = []

        def publish(self, msg):
            self.messages.append(msg)

    node = McapRecorderNode.__new__(McapRecorderNode)
    node._record_stop_publisher = _Publisher()
    node._fsm_command_publisher = _Publisher()
    node.get_logger = lambda: _Logger()

    node._publish_start_blocked_signals()

    assert len(node._record_stop_publisher.messages) == 1
    assert [msg.data for msg in node._fsm_command_publisher.messages] == [FSM_HOLD_COMMAND]


def _make_stopped_episode(tmp_path, episode_id="episode_size_check"):
    from data_collection_core.session import EpisodeInfo

    episode_dir = tmp_path / episode_id
    recording_dir = episode_dir / "recording"
    recording_dir.mkdir(parents=True)
    return EpisodeInfo(
        episode_id=episode_id,
        episode_dir=episode_dir,
        recording_dir=recording_dir,
    )


def _make_size_check_node(
    episode,
    minimum_size_bytes,
    maximum_size_bytes=1_000_000_000,
):
    node = McapRecorderNode.__new__(McapRecorderNode)
    node._last_stopped_episode = episode
    node._minimum_episode_size_bytes = minimum_size_bytes
    node._minimum_episode_size_mb = minimum_size_bytes / 1_000_000
    node._maximum_episode_size_bytes = maximum_size_bytes
    node._maximum_episode_size_mb = maximum_size_bytes / 1_000_000
    node._save_threads = []
    node._save_threads_lock = __import__("threading").Lock()
    node._discard_lock = __import__("threading").Lock()
    node.get_logger = lambda: _Logger()
    return node


def test_small_previous_episode_blocks_start_and_publishes_stop(tmp_path):
    episode = _make_stopped_episode(tmp_path)
    (episode.recording_dir / "recording_0.mcap").write_bytes(b"small")
    node = _make_size_check_node(episode, minimum_size_bytes=10)
    stop_signals = []
    node._publish_start_blocked_signals = lambda: stop_signals.append("stop")

    assert node._previous_episode_allows_start() is False
    assert stop_signals == ["stop"]
    assert node._last_stopped_episode == episode


def test_small_previous_episode_does_not_create_next_episode(tmp_path):
    class _StoppedSession:
        is_recording = False

        def __init__(self):
            self.start_calls = []

        def start(self, timestamp_ns):
            self.start_calls.append(timestamp_ns)

    episode = _make_stopped_episode(tmp_path)
    (episode.recording_dir / "recording_0.mcap").write_bytes(b"small")
    node = _make_size_check_node(episode, minimum_size_bytes=10)
    node.session = _StoppedSession()
    node._publish_start_blocked_signals = lambda: None

    node._start_recording(timestamp_ns=123)

    assert node.session.start_calls == []


def test_previous_episode_at_minimum_size_allows_start(tmp_path):
    episode = _make_stopped_episode(tmp_path)
    (episode.recording_dir / "recording_0.mcap").write_bytes(b"large-enough")
    node = _make_size_check_node(episode, minimum_size_bytes=12)
    node._publish_start_blocked_signals = lambda: None

    assert node._previous_episode_allows_start() is True


def test_previous_episode_over_maximum_size_blocks_start(tmp_path):
    episode = _make_stopped_episode(tmp_path)
    (episode.recording_dir / "recording_0.mcap").write_bytes(b"too-large")
    node = _make_size_check_node(
        episode,
        minimum_size_bytes=1,
        maximum_size_bytes=8,
    )
    stop_signals = []
    node._publish_start_blocked_signals = lambda: stop_signals.append("stop")

    assert node._previous_episode_allows_start() is False
    assert stop_signals == ["stop"]
    assert node._last_stopped_episode == episode


def test_previous_episode_at_maximum_size_allows_start(tmp_path):
    episode = _make_stopped_episode(tmp_path)
    (episode.recording_dir / "recording_0.mcap").write_bytes(b"max-size")
    node = _make_size_check_node(
        episode,
        minimum_size_bytes=1,
        maximum_size_bytes=8,
    )
    node._publish_start_blocked_signals = lambda: None

    assert node._previous_episode_allows_start() is True


def test_previous_episode_still_saving_blocks_start(tmp_path):
    class _SavingThread:
        name = "mcap-save-episode_size_check"

        def is_alive(self):
            return True

    episode = _make_stopped_episode(tmp_path)
    node = _make_size_check_node(episode, minimum_size_bytes=10)
    node._save_threads = [_SavingThread()]
    stop_signals = []
    node._publish_start_blocked_signals = lambda: stop_signals.append("stop")

    assert node._previous_episode_allows_start() is False
    assert stop_signals == ["stop"]


def test_deleted_previous_episode_allows_start(tmp_path):
    from data_collection_core.session import EpisodeInfo

    episode_dir = tmp_path / "already_deleted"
    episode = EpisodeInfo(
        episode_id=episode_dir.name,
        episode_dir=episode_dir,
        recording_dir=episode_dir / "recording",
    )
    node = _make_size_check_node(episode, minimum_size_bytes=10)
    node._publish_start_blocked_signals = lambda: None

    assert node._previous_episode_allows_start() is True
    assert node._last_stopped_episode is None


def test_uploader_checks_pending_uploads_on_startup(tmp_path):
    identity_file = tmp_path / "id_rsa"
    identity_file.write_text("fake-key", encoding="utf-8")
    config = UploadConfig(
        enabled=True,
        host="example.com",
        user="user",
        remote_base_dir="/data",
        identity_file=str(identity_file),
        max_parallel=1,
        check_pending_on_startup=True,
    )
    uploader = EpisodeUploader(config=config, output_dir=tmp_path)
    calls = []
    uploader._check_pending_uploads = lambda: calls.append("check")

    uploader.start()
    uploader.shutdown(timeout_sec=1.0)

    assert calls == ["check"]


def test_discard_command_without_stopped_episode_warns():
    node = McapRecorderNode.__new__(McapRecorderNode)
    node._last_stopped_episode = None
    node._save_threads = []
    node._save_threads_lock = __import__("threading").Lock()
    node._discard_lock = __import__("threading").Lock()
    node._convert_after_save_episode_ids = set()
    warnings = []
    node._collector_warn = lambda msg: warnings.append(msg)

    node._handle_discard_command()

    assert len(warnings) == 1


def test_discard_command_removes_last_stopped_episode(tmp_path):
    from data_collection_core.session import EpisodeInfo

    episode_dir = tmp_path / "20260716_120000_000000"
    recording_dir = episode_dir / "recording"
    recording_dir.mkdir(parents=True)
    episode = EpisodeInfo(
        episode_id=episode_dir.name,
        episode_dir=episode_dir,
        recording_dir=recording_dir,
    )

    node = McapRecorderNode.__new__(McapRecorderNode)
    node._last_stopped_episode = episode
    node._save_threads = []
    node._save_threads_lock = __import__("threading").Lock()
    node._discard_lock = __import__("threading").Lock()
    node._convert_after_save_episode_ids = set()
    node._uploader = None
    node.get_logger = lambda: _Logger()
    node._collector_warn = lambda *_args, **_kwargs: None

    node._handle_discard_command()

    assert not episode_dir.exists()
    assert node._last_stopped_episode is None


def test_controller_13_uses_same_discard_handler():
    class _ClockNow:
        nanoseconds = 123

    class _Clock:
        def now(self):
            return _ClockNow()

    class _Message:
        data = DISCARD_LAST_EPISODE_CODE

    node = McapRecorderNode.__new__(McapRecorderNode)
    calls = []
    node.get_clock = lambda: _Clock()
    node._handle_discard_command = lambda: calls.append("discard")

    node._handle_control_message(_Message())

    assert calls == ["discard"]


def test_recap_controller_13_uses_same_discard_handler():
    from data_collection_recap_recorder.recap_recorder_node import RecapMcapRecorderNode

    class _ClockNow:
        nanoseconds = 123

    class _Clock:
        def now(self):
            return _ClockNow()

    class _Message:
        data = DISCARD_LAST_EPISODE_CODE

    node = RecapMcapRecorderNode.__new__(RecapMcapRecorderNode)
    calls = []
    node.get_clock = lambda: _Clock()
    node._handle_discard_command = lambda: calls.append("discard")

    node._handle_control_message(_Message())

    assert calls == ["discard"]


def test_discard_requests_are_serialized(tmp_path):
    from data_collection_core.session import EpisodeInfo

    episode_dir = tmp_path / "20260716_120000_000001"
    recording_dir = episode_dir / "recording"
    recording_dir.mkdir(parents=True)
    episode = EpisodeInfo(
        episode_id=episode_dir.name,
        episode_dir=episode_dir,
        recording_dir=recording_dir,
    )

    node = McapRecorderNode.__new__(McapRecorderNode)
    node._last_stopped_episode = episode
    node._episode_waiting_for_next_collection = None
    node._save_threads = []
    node._save_threads_lock = __import__("threading").Lock()
    node._discard_lock = __import__("threading").Lock()
    node._convert_after_save_episode_ids = set()
    warnings = []
    discarded = []
    node._collector_warn = lambda msg: warnings.append(msg)
    node._discard_episode = lambda item: discarded.append(item.episode_id)

    threads = [
        __import__("threading").Thread(target=node._handle_discard_command)
        for _ in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert discarded == [episode.episode_id]
    assert len(warnings) == 1


def test_uploader_discard_marks_skip_and_removes_remote(tmp_path, monkeypatch):
    identity_file = tmp_path / "id_rsa"
    identity_file.write_text("fake-key", encoding="utf-8")
    config = UploadConfig(
        enabled=True,
        host="example.com",
        user="user",
        remote_base_dir="/data",
        identity_file=str(identity_file),
    )
    episode_dir = tmp_path / "episode_1"
    episode_dir.mkdir()

    uploader = EpisodeUploader(config=config, output_dir=tmp_path)
    removed = []
    uploader._remove_remote_dir = lambda remote_dir: removed.append(remote_dir)

    uploader.discard(episode_dir)

    assert str(episode_dir.resolve()) in uploader._discarded
    assert removed == [f"user@example.com:/data/{episode_dir.name}"]


def test_lerobot_converter_runs_convert_script(tmp_path):
    script = tmp_path / "convert.sh"
    calls = tmp_path / "calls.txt"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$@\" > \"$CALLS_FILE\"\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    episode_dir = tmp_path / "episode_1"
    episode_dir.mkdir()
    output_dir = tmp_path / "lerobot"
    config = LeRobotConversionConfig(
        enabled=True,
        script_path=str(script),
        output_dir=str(output_dir),
    )
    converter = LeRobotEpisodeConverter(config=config)

    env = __import__("os").environ
    old_calls_file = env.get("CALLS_FILE")
    env["CALLS_FILE"] = str(calls)
    try:
        converter._convert_episode(episode_dir)
    finally:
        if old_calls_file is None:
            env.pop("CALLS_FILE", None)
        else:
            env["CALLS_FILE"] = old_calls_file

    assert calls.read_text(encoding="utf-8").splitlines() == [
        "--input-root",
        str(episode_dir),
        "--output-dir",
        str(output_dir.resolve()),
    ]

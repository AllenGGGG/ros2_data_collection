from data_collection_recorder.recorder_node import McapRecorderNode
from data_collection_core.constants import DISCARD_LAST_EPISODE_CODE
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

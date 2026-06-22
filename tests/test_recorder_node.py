from data_collection_recorder.recorder_node import McapRecorderNode


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

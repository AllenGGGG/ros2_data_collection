START_RECORDING_CODE = 4
STOP_RECORDING_CODE = 14
INFERENCE_RESUMED_CODE = 30
INFERENCE_PAUSED_CODE = 31

DEFAULT_CONTROL_TOPIC = '/xr/controller_state'
RECORD_STOP_TOPIC = '/ros2recordstop'
DEFAULT_PROFILE_PATH = 'config/recording/default_profile.yaml'
DEFAULT_STORAGE_CONFIG_PATH = 'config/recording/mcap_storage.yaml'

# High-rate arm state topics; default record_max_hz comes from profile state_record_max_hz.
STATE_RECORD_TOPICS = frozenset({
    '/joint_states',
    '/left_current_pose',
    '/right_current_pose',
})

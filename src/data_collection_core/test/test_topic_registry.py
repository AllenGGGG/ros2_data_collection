from data_collection_core.topic_registry import TopicProfile


def test_profile_loads_output_dir_from_yaml(tmp_path):
    profile_path = tmp_path / 'profile.yaml'
    profile_path.write_text(
        '\n'.join([
            'output_dir: ~/custom_bags',
            'topics:',
            '  - name: /joint_states',
            '    type: sensor_msgs/msg/JointState',
        ]),
        encoding='utf-8',
    )

    profile = TopicProfile.from_yaml(profile_path)

    assert profile.output_dir == '~/custom_bags'

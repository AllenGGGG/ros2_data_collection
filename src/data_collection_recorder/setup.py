import os
from glob import glob

from setuptools import find_packages, setup

package_name = 'data_collection_recorder'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        (os.path.join('share', package_name, 'config', 'recording'), glob('config/recording/*.yaml')),
        (os.path.join('share', package_name, 'launch'), glob('launch/*.launch.py')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='zihang',
    maintainer_email='zihang@todo.todo',
    description='ROS 2 recorder node that writes data collection episodes as MCAP bags.',
    license='TODO',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'mcap_recorder = data_collection_recorder.recorder_node:main',
        ],
    },
)

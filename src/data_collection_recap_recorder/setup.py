import os
from glob import glob

from setuptools import find_packages, setup

package_name = 'data_collection_recap_recorder'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        (os.path.join('share', package_name, 'config', 'recording'), glob('config/recording/*.yaml')),
        (os.path.join('share', package_name, 'config', 'inference'), glob('config/inference/*.yaml')),
        (os.path.join('share', package_name, 'launch'), glob('launch/*.launch.py')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='zihang',
    maintainer_email='zihang@todo.todo',
    description='Recap inference takeover MCAP recorder and launch integration.',
    license='TODO',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'recap_mcap_recorder = data_collection_recap_recorder.recap_recorder_node:main',
        ],
    },
)

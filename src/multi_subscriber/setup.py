from setuptools import find_packages, setup

package_name = 'multi_subscriber'

setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=[
        'setuptools',
        'numpy',
        'pandas',
        'opencv-python',
    ],
    zip_safe=True,
    maintainer='zihang',
    maintainer_email='zihang@todo.todo',
    description='TODO: Package description',
    license='TODO: License declaration',
    extras_require={
        'test': [
            'pytest',
        ],
    },
    entry_points={
    'console_scripts': [
        'multi_topic_subscriber = multi_subscriber.multi_subscriber:main',
        'isaac_test_subscriber = multi_subscriber.test:main',
        'multi_topic_subscriber_sim_real = multi_subscriber.multi_subscriber_sim_real:main',
        ],
    },

)
